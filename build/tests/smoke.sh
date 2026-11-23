#!/bin/sh
# Run on a Docker host: sh build/tests/smoke.sh IMAGE
set -eu
image=${1:?Pass the image to test}
name="code-server-smoke-$$"
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
project_dir=$(cd -- "$script_dir/../.." && pwd)
# Validate settings before creating anything that cleanup must remove.
home_storage=${TEST_HOME_STORAGE:-bind}
if [ "$(uname -s)" = Darwin ]; then home_storage=volume; fi
case "$home_storage" in
    bind|volume) ;;
    *) echo 'TEST_HOME_STORAGE must be bind or volume.' >&2; exit 1 ;;
esac
home_dir=$(mktemp -d "${TEST_TMPDIR:-$project_dir}/.test-tmp.XXXXXXXX")
chmod 755 "$home_dir"
home_mount="type=bind,src=$home_dir,dst=/home"
# macOS shared folders cannot represent all Linux ownership semantics. Production
# uses named volumes too; act passes this choice into its Linux runner on macOS.
# Keep the empty bind-mount coverage on native Linux.
if [ "$home_storage" = volume ]; then
    home_mount="type=volume,src=$name-home,dst=/home,volume-nocopy"
fi
cleanup() {
    result=$?
    if [ "$result" -ne 0 ]; then docker logs "$name" >&2 || true; fi
    docker rm -f -v "$name" >/dev/null 2>&1 || true
    docker run --rm --network none --mount "type=bind,src=$home_dir,dst=/test-home" \
        --entrypoint /bin/bash "$image" -ec 'find /test-home -mindepth 1 -delete' >/dev/null
    rmdir "$home_dir"
    docker volume rm "$name-docker" >/dev/null 2>&1 || true
    if [ "$home_storage" = volume ]; then
        docker volume rm "$name-home" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
create_workspace() {
    # Match compose.yaml's isolation, shutdown timeout, and log console.
    docker create -t --name "$name" --cgroupns private --init=false --stop-timeout 180 \
        --cap-add SYS_ADMIN --cap-add NET_ADMIN --security-opt apparmor=unconfined \
        --security-opt seccomp=unconfined --security-opt systempaths=unconfined \
        -e CF_ACCESS_TEAM_DOMAIN=test.cloudflareaccess.com \
        -e CF_ACCESS_AUD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
        -e AIO_RELEASE_TAG=v0.0.0 -e AIO_RELEASE_REPOSITORY=workspace-smoke/workspace-smoke \
        --mount "$home_mount" --mount "type=volume,src=$name-docker,dst=/var/lib/docker" \
        "$@" "$image" >/dev/null
}
start_workspace() {
    create_workspace "$@"
    docker start "$name" >/dev/null
}
wait_ready() {
    deadline=$(($(date +%s) + 120))
    until docker exec "$name" /usr/local/bin/container-healthcheck >/dev/null 2>&1; do
        if [ "$(date +%s)" -gt "$deadline" ]; then
            docker exec "$name" /usr/local/bin/container-healthcheck || true
            return 1
        fi
        sleep 2
    done
}
wait_for_restart() {
    previous_restarts=$1
    deadline=$(($(date +%s) + 120))
    until [ "$(docker inspect -f '{{.RestartCount}}' "$name")" -gt "$previous_restarts" ]; do
        if [ "$(date +%s)" -gt "$deadline" ]; then return 1; fi
        sleep 2
    done
    wait_ready
}
brew_shell() {
    # Use the environment supplied to code-server, as an IDE terminal would.
    docker exec -i --user cloud-user \
        -e PATH=/home/linuxbrew/.linuxbrew/bin:/usr/local/bin:/usr/bin:/bin \
        -e HOMEBREW_NO_AUTO_UPDATE=1 -e HOMEBREW_NO_ANALYTICS=1 -e HOMEBREW_NO_INSTALL_FROM_API=1 \
        "$name" /bin/bash -ec '
            pid=$(systemctl show -p MainPID --value code-server)
            environment=$(tr "\000" "\n" < "/proc/$pid/environ")
            XDG_RUNTIME_DIR=$(sed -n "s/^XDG_RUNTIME_DIR=//p" <<< "$environment")
            DBUS_SESSION_BUS_ADDRESS=$(sed -n "s/^DBUS_SESSION_BUS_ADDRESS=//p" <<< "$environment")
            test "$XDG_RUNTIME_DIR" = /run/user/10000
            test "$DBUS_SESSION_BUS_ADDRESS" = unix:path=/run/user/10000/bus
            export XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS
            exec /bin/bash -ec "$1"
        ' brew-shell "$1"
}
assert_brew_service() {
    # Probe as root without opening a workspace login or invoking Homebrew.
    docker exec "$name" timeout 15 /bin/sh -ec '
        until [ "$(curl --disable --noproxy "*" -fsS --max-time 1 http://127.0.0.1:11211/ready 2>/dev/null)" = ready ]; do
            sleep 0.2
        done
    '
}
stop_workspace() {
    docker stop "$name" >/dev/null
    test "$(docker inspect -f '{{.State.ExitCode}}' "$name")" = 0
}
# Fail the runtime-directory dependency on cold boot. The sentinel outlives PID 1.
create_workspace --restart unless-stopped
runtime_dir_fault="$home_dir/user-runtime-dir@10000.service.d"
mkdir "$runtime_dir_fault"
cat > "$runtime_dir_fault/smoke.conf" <<'UNIT'
[Service]
ExecStartPre=/bin/sh -ec 'test -f /var/lib/smoke-runtime-dir-failed || { touch /var/lib/smoke-runtime-dir-failed; exit 1; }'
UNIT
docker cp "$runtime_dir_fault" "$name:/etc/systemd/system/"
rm "$runtime_dir_fault/smoke.conf"
rmdir "$runtime_dir_fault"
docker start "$name" >/dev/null
wait_for_restart 0
docker exec "$name" test -f /var/lib/smoke-runtime-dir-failed
startup_restarts=$(docker inspect -f '{{.RestartCount}}' "$name")
echo 'PASS: A runtime-directory failure on cold boot restarted the workspace.'
machine_id=$(docker exec "$name" cat /etc/machine-id)
printf '%s\n' "$machine_id" | grep -Eq '^[0-9a-f]{32}$'
test "$machine_id" = "$(docker exec "$name" cat /var/lib/dbus/machine-id)"
docker exec "$name" /bin/bash -ec '
    test "$(cat /proc/1/comm)" = systemd
    test "$(systemctl is-enabled supervisor.service || true)" = masked
    test ! -S /run/supervisor.sock
    test "$(loginctl show-user cloud-user -p Linger --value)" = yes
    test "$(stat -c %u:%g:%a /run/user/10000)" = 10000:10000:700
    for unit in code-server.service dockerd.service user@10000.service user-10000.slice; do
        test "$(systemctl show -p TasksMax --value "$unit")" = infinity
    done
    systemd-analyze verify /etc/systemd/system/{dockerd,code-server,envoy,update-notice}.service
    journalctl -b -u systemd-journald -o cat --no-pager | grep -E "Runtime Journal.*max 16M(,|$)"
    journalctl -b -u systemd-journald -o cat --no-pager | grep -E "System Journal.*max 32M(,|$)"
    settings=$(mktemp -d)
    trap '"'"'rm -rf -- "$settings"'"'"' EXIT
    mkdir "$settings/docker"
    printf "fail\n" > "$settings/.curlrc"
    printf "%s\n" "{\"currentContext\":\"workspace-context\"}" > "$settings/docker/config.json"
    HOME="$settings" CURL_HOME="$settings" DOCKER_CONFIG="$settings/docker" \
        DOCKER_CONTEXT=workspace-context DOCKER_HOST=unix:///run/unreachable-docker.sock \
        /usr/local/bin/container-healthcheck
'
docker exec "$name" /bin/bash -ec '
    # Guard the workspace prerequisites used by Linux Homebrew.
    for executable in gcc make git curl file ps sudo; do
        command -v "$executable" >/dev/null
    done
    test "$(sudo -u cloud-user sudo -n id -u)" = 0
    # Nested Docker must run containers, without host block devices exposed.
    sudo -u cloud-user docker run --rm debian:13-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 true
    limited=$(sudo -u cloud-user docker run -d --rm --pids-limit 32 \
        debian:13-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 sleep infinity)
    test "$(systemctl show -p TasksMax --value "docker-$limited.scope")" = 32
    sudo -u cloud-user docker rm -f "$limited" >/dev/null
    test ! -e /dev/sda -a ! -e /dev/nvme0n1 -a ! -e /dev/vda
    test "$(stat -c %u:%g /home/cloud-user)" = 10000:10000
    test "$(stat -c %a /run/code-server/backend.sock)" = 660
    test "$(stat -c %u:%g /run/code-server/backend.sock)" = 10000:10000
    test "$(curl --silent --max-time 2 --output /dev/null --write-out "%{http_code}" http://127.0.0.1:8080/smoke-access-log)" = 403
    envoy_pid=$(systemctl show -p MainPID --value envoy)
    test "$(stat -c %U /proc/$envoy_pid)" = envoy
    test "$(stat -c %a /run/envoy/admin.sock)" = 600
    test "$(stat -c %U /run/envoy/admin.sock)" = envoy
    if sudo -u cloud-user curl --silent --max-time 2 --unix-socket /run/envoy/admin.sock http://localhost/ready; then exit 1; fi
    if curl --silent --max-time 1 http://127.0.0.1:8081/; then exit 1; fi
    pid=$(systemctl show -p MainPID --value code-server)
    grep -q "^Uid:[[:space:]]*10000" "/proc/$pid/status"
'
docker logs "$name" 2>&1 | grep 'HTTP server listening on /run/code-server/backend.sock' >/dev/null
docker logs "$name" 2>&1 | python3 -c '
import json
import sys

record = next(line for line in sys.stdin if "/smoke-access-log" in line)
entry = json.loads(record)
assert (entry["path"], entry["status"]) == ("/smoke-access-log", 403)
'
# The user manager must recover from a one-time failure without restarting the workspace.
docker exec -i "$name" /bin/sh -es <<'SH'
mkdir -p /run/systemd/system/user@10000.service.d
cat > /run/systemd/system/user@10000.service.d/smoke.conf <<'UNIT'
[Service]
ExecStartPre=/bin/sh -ec 'test -f /home/cloud-user/.smoke-user-failed || { touch /home/cloud-user/.smoke-user-failed; exit 1; }'
UNIT
systemctl daemon-reload
systemctl restart user@10000.service || test -f /home/cloud-user/.smoke-user-failed
timeout 30 /bin/sh -c 'until test "$(systemctl show -p NRestarts --value user@10000.service)" -gt 0; do sleep 0.2; done'
SH
wait_ready
test "$(docker inspect -f '{{.RestartCount}}' "$name")" = "$startup_restarts"

brew_shell '/bin/sh -s' < "$script_dir/install-homebrew-fixture.sh"
# shellcheck disable=SC2016 # Check the user unit inside the workspace.
brew_shell '
    brew services start workspace/smoke/workspace-smoke
    brew services list | grep -E "^workspace-smoke[[:space:]]+started[[:space:]]+cloud-user"
    test "$(systemctl --user show -p TasksMax --value sh.brew.workspace-smoke.service)" = infinity
'
assert_brew_service
echo 'PASS: Homebrew started a user service using the code-server environment.'

# Fail two D-Bus activations. The first is slow enough to test readiness
# while the socket exists. The two markers survive the user-manager restart.
bus_restarts=$(docker exec "$name" systemctl show -p NRestarts --value user@10000.service)
docker exec -i "$name" /bin/sh -es <<'SH'
mkdir -p /run/systemd/user/dbus.service.d
cat > /run/systemd/user/dbus.service.d/smoke.conf <<'UNIT'
[Unit]
StartLimitIntervalSec=30s
StartLimitBurst=2

[Service]
ExecStartPre=/bin/sh -ec '\
    if [ ! -e /home/cloud-user/.smoke-dbus-first ]; then \
        touch /home/cloud-user/.smoke-dbus-first; sleep 5; exit 1; \
    fi; \
    if [ ! -e /home/cloud-user/.smoke-dbus-second ]; then \
        touch /home/cloud-user/.smoke-dbus-second; exit 1; \
    fi'
UNIT
SH
brew_shell '
    systemctl --user daemon-reload
    systemctl --user stop dbus.service
    systemctl --user reset-failed dbus.service
    systemctl --user is-active --quiet dbus.socket
    test -S /run/user/10000/bus
'
docker exec "$name" /bin/sh -ec '
    if output=$(/usr/local/bin/container-healthcheck 2>&1); then exit 1; fi
    printf "%s\n" "$output" | grep -F "Workspace user bus is not ready"
'
docker exec "$name" timeout 30 /bin/sh -c '
    until test "$(systemctl show -p NRestarts --value user@10000.service)" -gt "$1"; do
        /usr/local/bin/container-healthcheck >/dev/null 2>&1 || true
        sleep 0.2
    done
' wait-for-user-manager "$bus_restarts"
wait_ready
assert_brew_service
test "$(docker inspect -f '{{.RestartCount}}' "$name")" = "$startup_restarts"
docker exec "$name" /bin/sh -ec '
    test -f /home/cloud-user/.smoke-dbus-second
    rm /run/systemd/user/dbus.service.d/smoke.conf
'
brew_shell 'systemctl --user daemon-reload'
echo 'PASS: Health detected the D-Bus failure and systemd recovered the user manager and enabled services.'

# A persistent user-manager failure must exit PID 1 after bounded retries.
docker exec "$name" /bin/sh -ec '
    printf "[Service]\nExecStartPre=/usr/bin/false\n" > /run/systemd/system/user@10000.service.d/smoke.conf
    systemctl daemon-reload
    systemctl reset-failed user@10000.service
    systemctl restart --no-block user@10000.service
'
wait_for_restart "$startup_restarts"
assert_brew_service
before_daemon_recovery=$(docker inspect -f '{{.RestartCount}}' "$name")
echo 'PASS: The user manager retried a transient failure and restarted the workspace after repeated failures.'

# An optional unit failure must leave the workspace healthy.
docker exec "$name" /bin/bash -ec '
    systemctl is-active --quiet update-notice
    mkdir -p /run/systemd/system/update-notice.service.d
    printf "[Service]\nExecStart=\nExecStart=/usr/bin/false\nRestart=no\n" \
        > /run/systemd/system/update-notice.service.d/smoke.conf
    systemctl daemon-reload
    systemctl restart update-notice || true
    timeout 10 /bin/sh -c "until systemctl is-failed --quiet update-notice; do sleep 0.2; done"
    /usr/local/bin/container-healthcheck
'

# A transient daemon crash recovers in place. A crash loop reaches the unit start
# limit and exits PID 1. The injected drop-in disappears on container restart.
docker exec "$name" /bin/bash -ec '
    systemctl kill --signal=SIGKILL --kill-whom=main dockerd
    timeout 30 /bin/sh -c "until test \"\$(systemctl show -p NRestarts --value dockerd)\" -gt 0; do sleep 0.2; done"
'
wait_ready
test "$(docker inspect -f '{{.RestartCount}}' "$name")" = "$before_daemon_recovery"
docker exec "$name" /bin/bash -ec '
    mkdir -p /run/systemd/system/dockerd.service.d
    printf "[Service]\nExecStart=\nExecStart=/usr/bin/false\n" > /run/systemd/system/dockerd.service.d/smoke.conf
    systemctl daemon-reload
    systemctl reset-failed dockerd
    systemctl restart --no-block dockerd
'
wait_for_restart "$before_daemon_recovery"
assert_brew_service
echo 'PASS: A daemon crash recovered in place and a crash loop restarted the workspace.'
docker exec "$name" /bin/bash -ec '
    # Keep a workload across the orderly stop and offline recreation below.
    nested=$(sudo -u cloud-user docker run -d --name smoke-nested --network none --restart unless-stopped \
        debian:13-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 sleep infinity)
    test "$(systemctl show -p TasksMax --value "docker-$nested.scope")" = infinity
    touch /home/cloud-user/.ownership-sentinel
    chown 999:999 /home/cloud-user/.ownership-sentinel
    mkdir -p /home/cloud-user/.local/bin
    # A user-installed Python must never replace Debian Python for infrastructure.
    for helper in python3 containerd runc iptables docker-proxy; do
        printf "#!/bin/sh\nexit 86\n" > "/home/cloud-user/.local/bin/$helper"
        chmod +x "/home/cloud-user/.local/bin/$helper"
    done
    # Root Python services must never execute .pth files from the user site.
    pyver=$(/usr/bin/python3 -c "import sys; print(\"python%d.%d\" % sys.version_info[:2])")
    user_site="/home/cloud-user/.local/lib/$pyver/site-packages"
    mkdir -p "$user_site"
    printf "import sys; sys.exit(86)\n" > "$user_site/zz-smoke-hijack.pth"
    chown -R 10000:10000 /home/cloud-user/.local
'
stop_workspace
docker rm -v "$name" >/dev/null

# Recreate offline with the same home and daemon data, without a console TTY.
start_workspace --network none --tty=false \
    -e HTTP_PROXY=http://proxy.example.invalid:3128 -e HTTPS_PROXY=http://proxy.example.invalid:3128 \
    -e NO_PROXY=localhost,127.0.0.1 -e http_proxy=http://lower-proxy.example.invalid:3128 \
    -e https_proxy=http://lower-proxy.example.invalid:3128 -e no_proxy=localhost
wait_ready
docker exec "$name" /bin/sh -ec '
    test "$(docker info --format "{{.HTTPProxy}}|{{.HTTPSProxy}}|{{.NoProxy}}")" = \
        "http://proxy.example.invalid:3128|http://proxy.example.invalid:3128|localhost,127.0.0.1"
    test "$(systemctl show -p StandardOutput --value envoy)" = journal
    test ! -e /dev/console
'
for service_user in code-server:cloud-user update-notice:envoy; do
    docker exec --user "${service_user#*:}" "$name" /bin/sh -ec '
        pid=$(systemctl show -p MainPID --value "$1")
        environment=$(tr "\000" "\n" < "/proc/$pid/environ")
        for key in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
            value=$(printenv "$key")
            printf "%s\n" "$environment" | grep -Fx "$key=$value" >/dev/null
        done
    ' proxy-environment "${service_user%:*}"
done
echo 'PASS: Nested Docker, code-server, and update notices inherit proxy settings.'
echo 'PASS: Envoy uses the journal without a TTY.'
recreated_machine_id=$(docker exec "$name" cat /etc/machine-id)
printf '%s\n' "$recreated_machine_id" | grep -Eq '^[0-9a-f]{32}$'
test "$recreated_machine_id" != "$machine_id"
assert_brew_service
brew_shell '
    brew services list | grep -E "^workspace-smoke[[:space:]]+started[[:space:]]+cloud-user"
    brew services stop workspace/smoke/workspace-smoke
'
stop_workspace
docker start "$name" >/dev/null
wait_ready
test "$(docker exec "$name" cat /etc/machine-id)" = "$recreated_machine_id"
brew_shell '
    brew services list | grep -E "^workspace-smoke[[:space:]]+none"
    if timeout 2 /bin/bash -c "echo ready > /dev/tcp/127.0.0.1/11211" 2>/dev/null; then exit 1; fi
'
echo 'PASS: The enabled service started after offline recreation and stayed stopped after disablement.'
docker exec "$name" /bin/bash -ec '
    test "$(stat -c %u:%g /home/cloud-user/.ownership-sentinel)" = 999:999
    pid=$(systemctl show -p MainPID --value dockerd)
    daemon_path=$(tr "\0" "\n" < "/proc/$pid/environ" | sed -n "s/^PATH=//p")
    test "$daemon_path" = /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
    for helper in containerd runc iptables docker-proxy; do
        resolved=$(PATH="$daemon_path" command -v "$helper" || true)
        case "$resolved" in /home/*) exit 1 ;; esac
    done
    /usr/local/bin/container-healthcheck
    # Restarting code-server replaces its stale socket without repeating setup.
    systemctl restart code-server
'
wait_ready
docker exec "$name" /bin/bash -ec '
    if [ "$(docker inspect -f "{{.State.Running}}" smoke-nested)" != true ]; then
        docker inspect -f "{{json .State}}" smoke-nested >&2
        exit 1
    fi
    # A running daemon whose API is unreachable must not report healthy.
    mv /var/run/docker.sock /var/run/docker.sock.hidden
    if /usr/local/bin/container-healthcheck; then exit 1; fi
    mv /var/run/docker.sock.hidden /var/run/docker.sock
    /usr/local/bin/container-healthcheck
    systemctl stop dockerd
    if /usr/local/bin/container-healthcheck; then exit 1; fi
'
stop_workspace
docker rm -v "$name" >/dev/null

# Existing resolved Compose files still enable Docker init and omit the TTY.
start_workspace --init=true --tty=false --network none
wait_ready
docker exec "$name" /bin/sh -ec '
    test "$(cat /proc/1/comm)" = docker-init
    supervisorctl status dockerd code-server envoy
    test ! -S /run/user/10000/bus
'
stop_workspace
echo 'PASS: The new image starts and stops with the previous Compose init settings.'
echo 'Full-container smoke tests passed.'
