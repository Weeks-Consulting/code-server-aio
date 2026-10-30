#!/bin/sh
# Run on a Docker host: sh build/tests/smoke.sh IMAGE
set -eu
image=${1:?Pass the image to test}
name="code-server-smoke-$$"
project_dir=$(cd -- "$(dirname -- "$0")/../.." && pwd)
home_dir=$(mktemp -d "${TEST_TMPDIR:-$project_dir}/.test-tmp.XXXXXXXX")
chmod 755 "$home_dir"
home_mount="type=bind,src=$home_dir,dst=/home"
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
fi
cleanup() {
    result=$?
    if [ "$result" -ne 0 ]; then docker logs "$name" >&2 || true; fi
    docker rm -f "$name" >/dev/null 2>&1 || true
    docker run --rm --network none --mount "type=bind,src=$home_dir,dst=/test-home" \
        --entrypoint /bin/bash "$image" -ec 'find /test-home -mindepth 1 -delete' >/dev/null
    rmdir "$home_dir"
    if [ "$home_storage" = volume ]; then
        docker volume rm "$name-home" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Keep in sync with compose.yaml; the workspace must not need --privileged.
run_flags="--cap-add SYS_ADMIN --cap-add NET_ADMIN --device /dev/fuse --security-opt apparmor=unconfined --security-opt seccomp=unconfined --security-opt systempaths=unconfined"
wait_ready() {
    deadline=$(($(date +%s) + 720))
    until docker exec "$name" /bin/bash -ec '
        /usr/local/bin/container-healthcheck
    ' >/dev/null 2>&1; do
        if [ "$(date +%s)" -gt "$deadline" ]; then docker logs "$name"; return 1; fi
        sleep 2
    done
}
# shellcheck disable=SC2086 # run_flags is intentionally word-split.
docker run -d --name "$name" $run_flags --init \
    -e CF_ACCESS_TEAM_DOMAIN=test.cloudflareaccess.com \
    -e CF_ACCESS_AUD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --restart unless-stopped \
    --mount "$home_mount" "$image" >/dev/null
wait_ready
docker exec "$name" /bin/bash -ec '
    # Guard the workspace prerequisites used by Linux Homebrew.
    for executable in gcc make git curl file ps sudo; do
        command -v "$executable" >/dev/null
    done
    test "$(sudo -u cloud-user sudo -n id -u)" = 0
    # Nested Docker must run containers, without host block devices exposed.
    sudo -u cloud-user docker run --rm debian:13-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4 true
    test ! -e /dev/sda -a ! -e /dev/nvme0n1 -a ! -e /dev/vda
    test "$(stat -c %u:%g /home/cloud-user)" = 10000:10000
    test "$(stat -c %a /run/code-server/backend.sock)" = 660
    test "$(stat -c %u:%g /run/code-server/backend.sock)" = 10000:10000
    test "$(curl --silent --max-time 2 --output /dev/null --write-out "%{http_code}" http://127.0.0.1:8080/)" = 403
    envoy_pid=$(/usr/bin/supervisorctl pid envoy)
    test "$(stat -c %U /proc/$envoy_pid)" = envoy
    test "$(stat -c %a /run/envoy/admin.sock)" = 600
    test "$(stat -c %U /run/envoy/admin.sock)" = envoy
    if sudo -u cloud-user curl --silent --max-time 2 --unix-socket /run/envoy/admin.sock http://localhost/ready; then exit 1; fi
    if curl --silent --max-time 1 http://127.0.0.1:8081/; then exit 1; fi
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
    mkdir -p /home/cloud-user/.local/bin
    # A user-installed Python must never replace Debian Python for infrastructure.
    for helper in python3 containerd runc iptables docker-proxy; do
        printf "#!/bin/sh\nexit 86\n" > "/home/cloud-user/.local/bin/$helper"
        chmod +x "/home/cloud-user/.local/bin/$helper"
    done
'
docker rm -f "$name" >/dev/null

# Reuse the populated home offline; startup must preserve existing file owners.
# shellcheck disable=SC2086 # run_flags is intentionally word-split.
docker run -d --name "$name" $run_flags --init \
    -e CF_ACCESS_TEAM_DOMAIN=test.cloudflareaccess.com \
    -e CF_ACCESS_AUD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --network none \
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
    /usr/local/bin/container-healthcheck
    # Restarting code-server replaces its stale socket without repeating setup.
    /usr/bin/supervisorctl restart code-server
    /usr/local/bin/container-healthcheck
    # A running daemon whose API is unreachable must not report healthy.
    mv /var/run/docker.sock /var/run/docker.sock.hidden
    if /usr/local/bin/container-healthcheck; then exit 1; fi
    mv /var/run/docker.sock.hidden /var/run/docker.sock
    /usr/local/bin/container-healthcheck
    /usr/bin/supervisorctl stop dockerd
    if /usr/local/bin/container-healthcheck; then exit 1; fi
'
echo 'Full-container smoke tests passed.'
