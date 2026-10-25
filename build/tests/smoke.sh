#!/bin/sh
# Run on a Docker host: sh build/tests/smoke.sh IMAGE
set -eu
image=${1:?Pass the image to test}
name="code-server-smoke-$$"
project_dir=$(cd -- "$(dirname -- "$0")/../.." && pwd)
home_dir=$(mktemp -d "${TEST_TMPDIR:-$project_dir}/.test-tmp.XXXXXXXX")
chmod 755 "$home_dir"
home_mount="type=bind,src=$home_dir,dst=/home"
timeout_mount="type=bind,src=$home_dir/timeout-home,dst=/home"
home_storage=${TEST_HOME_STORAGE:-bind}
if [ "$(uname -s)" = Darwin ]; then home_storage=volume; fi
case "$home_storage" in
    bind|volume) ;;
    *) echo 'TEST_HOME_STORAGE must be bind or volume.' >&2; exit 1 ;;
esac
# macOS shared folders cannot represent all Linux ownership semantics. Production
# uses named volumes too; act passes this choice into its Linux runner on macOS.
# Keep the empty bind-mount coverage on native Linux.
if [ "$home_storage" = volume ]; then
    home_mount="type=volume,src=$name-home,dst=/home,volume-nocopy"
    timeout_mount="type=volume,src=$name-timeout,dst=/home,volume-nocopy"
fi
cleanup() {
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run --rm --network none --mount "type=bind,src=$home_dir,dst=/test-home" \
        --entrypoint /bin/bash "$image" -ec 'find /test-home -mindepth 1 -delete' >/dev/null
    rmdir "$home_dir"
    if [ "$home_storage" = volume ]; then
        docker volume rm "$name-home" "$name-timeout" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
wait_ready() {
    deadline=$(($(date +%s) + 720))
    until docker exec "$name" /bin/bash -ec '
        /usr/bin/supervisorctl status
        /usr/bin/docker info >/dev/null 2>&1
        /usr/bin/curl --fail --silent http://127.0.0.1:8081/healthz >/dev/null
        test "$(/usr/bin/curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/)" = 503
    ' >/dev/null 2>&1; do
        if [ "$(date +%s)" -gt "$deadline" ]; then docker logs "$name"; return 1; fi
        sleep 2
    done
}
docker run -d --name "$name" --privileged --init --restart unless-stopped \
    --mount "$home_mount" "$image" >/dev/null
wait_ready
docker exec "$name" /bin/bash -ec '
    test "$(stat -c %u:%g /home/cloud-user)" = 10000:10000
    /usr/bin/sudo -H -u cloud-user /bin/bash --login -ec "command -v brew; brew --version"
    pid=$(/usr/bin/supervisorctl pid code-server)
    grep -q "^Uid:[[:space:]]*10000" "/proc/$pid/status"
'
# A one-time daemon startup failure must trigger container restart, then recover.
docker exec -i "$name" /usr/bin/python3 <<'PY'
import configparser
path = '/etc/supervisor/supervisord.conf'
config = configparser.ConfigParser(interpolation=None)
config.read(path)
config['program:dockerd']['command'] = "/bin/bash -c 'if [ ! -f /run/smoke-daemon-failed ]; then touch /run/smoke-daemon-failed; exit 1; fi; exec /usr/sbin/dockerd --host=unix:///var/run/docker.sock --group=docker'"
config['program:dockerd']['startretries'] = '0'
with open(path, 'w') as output:
    config.write(output)
PY
docker exec "$name" /usr/bin/supervisorctl reread
docker exec "$name" /usr/bin/supervisorctl update dockerd || true
deadline=$(($(date +%s) + 180))
until [ "$(docker inspect -f '{{.RestartCount}}' "$name")" -gt 0 ]; do
    if [ "$(date +%s)" -gt "$deadline" ]; then docker logs "$name"; exit 1; fi
    sleep 2
done
wait_ready
docker logs "$name" 2>&1 | grep 'Critical service dockerd entered FATAL' >/dev/null
docker exec "$name" /bin/bash -ec '
    touch /home/cloud-user/.ownership-sentinel
    chown 999:999 /home/cloud-user/.ownership-sentinel
    # A user-installed Python must never replace Debian Python for infrastructure.
    for helper in python3 containerd runc iptables docker-proxy; do
        printf "#!/bin/sh\nexit 86\n" > "/home/linuxbrew/.linuxbrew/bin/$helper"
        chmod +x "/home/linuxbrew/.linuxbrew/bin/$helper"
    done
'
docker rm -f "$name" >/dev/null

# Reuse the populated home offline; startup must preserve existing file owners.
docker run -d --name "$name" --privileged --init --network none \
    --mount "$home_mount" "$image" >/dev/null
wait_ready
docker exec "$name" /bin/bash -ec '
    test "$(stat -c %u:%g /home/cloud-user/.ownership-sentinel)" = 999:999
    pid=$(/usr/bin/supervisorctl pid dockerd)
    daemon_path=$(tr "\0" "\n" < "/proc/$pid/environ" | sed -n "s/^PATH=//p")
    test "$daemon_path" = /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    for helper in containerd runc iptables docker-proxy; do
        resolved=$(PATH="$daemon_path" command -v "$helper" || true)
        case "$resolved" in /home/*) exit 1 ;; esac
    done
    test ! -f /home/linuxbrew/.homebrew-installing
    /usr/bin/sudo -H -u cloud-user /bin/bash --login -ec "brew --version"
    # The health check must detect missing Access configuration.
    if /usr/local/bin/container-healthcheck; then exit 1; fi
    # Install temporary trusted keys to check the healthy path against real code-server.
    /usr/bin/python3 -c '\''import hashlib,importlib.util,time
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
spec=importlib.util.spec_from_file_location("proxy","/usr/local/lib/access-proxy.py")
p=importlib.util.module_from_spec(spec); spec.loader.exec_module(p)
pem=rsa.generate_private_key(public_exponent=65537,key_size=2048).public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
key=p.RUNTIME/(hashlib.sha256(pem).hexdigest()+".pem")
key.write_bytes(pem)
p.install(p.configuration("https://test.cloudflareaccess.com","a"*64,[key],int(time.time())+3600))'\''
    /usr/local/bin/container-healthcheck
    /usr/bin/supervisorctl stop dockerd
    if /usr/local/bin/container-healthcheck; then exit 1; fi
'
if docker logs "$name" 2>&1 | grep 'Installing Homebrew' >/dev/null; then
    echo 'Offline startup attempted to reinstall Homebrew' >&2; exit 1
fi
# Validate timeout settings even when Homebrew is already installed.
docker run --rm --network none --tmpfs /home:mode=755 -e HOMEBREW_INSTALL_TIMEOUT=invalid \
    --entrypoint /bin/bash "$image" -ec '
        mkdir -p /home/linuxbrew/.linuxbrew/bin
        touch /home/linuxbrew/.linuxbrew/bin/brew
        chmod +x /home/linuxbrew/.linuxbrew/bin/brew
        if /usr/local/bin/entrypoint.sh --help > /tmp/result 2>&1; then exit 1; fi
        grep -q "HOMEBREW_INSTALL_TIMEOUT must be" /tmp/result
        printf "#!/bin/sh\ntouch /tmp/root-brew-executed\n" > /home/linuxbrew/.linuxbrew/bin/brew
        /bin/bash --login -c true
        test ! -e /tmp/root-brew-executed
    '
# A deliberately paused installer must leave the proxy available and backend closed.
docker rm -f "$name" >/dev/null
cat > "$home_dir/paused-installer.sh" <<'INSTALL'
#!/bin/bash
while [ ! -f /home/linuxbrew/continue-setup ]; do sleep 1; done
mkdir -p /home/linuxbrew/.linuxbrew/bin
printf '%s\n' '#!/bin/bash' 'echo fake-brew' > /home/linuxbrew/.linuxbrew/bin/brew
chmod +x /home/linuxbrew/.linuxbrew/bin/brew
INSTALL
docker run -d --name "$name" --privileged --init --network none --tmpfs /home:mode=755,exec \
    --mount "type=bind,src=$home_dir/paused-installer.sh,dst=/usr/local/lib/homebrew-install.sh,readonly" \
    -e HOMEBREW_INSTALL_TIMEOUT=600 "$image" >/dev/null
deadline=$(($(date +%s) + 60))
until docker exec "$name" test -f /home/linuxbrew/.homebrew-installing; do
    if [ "$(date +%s)" -gt "$deadline" ]; then docker logs "$name"; exit 1; fi
    sleep 1
done
docker exec "$name" /bin/bash -ec '
    test "$(curl -s -o /tmp/startup-page -w "%{http_code}" http://127.0.0.1:8080/)" = 503
    grep -q "Service is not ready" /tmp/startup-page
    grep -q http-equiv= /tmp/startup-page
    if curl --fail --silent http://127.0.0.1:8081/healthz; then exit 1; fi
    if /usr/local/bin/container-healthcheck; then exit 1; fi
    touch /home/linuxbrew/continue-setup
'
wait_ready
# A stalled installer must time out, retain the retry marker, and stop Supervisor.
docker rm -f "$name" >/dev/null
mkdir "$home_dir/timeout-home"
printf '#!/bin/bash\nsleep 60\n' > "$home_dir/timeout-installer.sh"
docker run -d --name "$name" --privileged --init --network none \
    --mount "$timeout_mount" \
    --mount "type=bind,src=$home_dir/timeout-installer.sh,dst=/usr/local/lib/homebrew-install.sh,readonly" \
    -e HOMEBREW_INSTALL_TIMEOUT=1 "$image" >/dev/null
deadline=$(($(date +%s) + 90))
until [ "$(docker inspect -f '{{.State.Running}}' "$name")" = false ]; do
    if [ "$(date +%s)" -gt "$deadline" ]; then docker logs "$name"; exit 1; fi
    sleep 1
done
docker logs "$name" 2>&1 | grep 'Workspace initialization failed (124)' >/dev/null
docker run --rm --network none --mount "$timeout_mount" --entrypoint /usr/bin/test \
    "$image" -f /home/linuxbrew/.homebrew-installing
echo 'Full-container smoke tests passed.'
