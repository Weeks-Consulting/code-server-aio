#!/bin/bash
# Readiness: all services running, nested Docker ready, workspace socket ready.
set -euo pipefail

not_ready() {
    printf 'Not ready: %s\n' "$1" >&2
    exit 1
}

status=$(timeout 3 /usr/bin/supervisorctl status service-events dockerd code-server envoy 2>&1) || not_ready "$status"
while read -r service state _; do
    case "$service:$state" in
        service-events:RUNNING|dockerd:RUNNING|code-server:RUNNING|envoy:RUNNING) ;;
        *) not_ready "services not ready: $status" ;;
    esac
done <<< "$status"

docker_info=$(timeout 3 /usr/bin/docker info --format '{{.ServerVersion}}' 2>&1) \
    || not_ready "Docker is not ready: $docker_info"

check_http() {
    local url=$1 expected=$2 code
    shift 2
    code=$(curl --noproxy '*' --silent --show-error --max-time 2 \
        --output /dev/null --write-out '%{http_code}' "$@" "$url") \
        || not_ready "Request to $url failed"
    [[ "$code" == "$expected" ]] \
        || not_ready "$url returned $code, expected $expected"
}

check_http http://localhost/healthz 200 --unix-socket /run/code-server/backend.sock --location
check_http http://127.0.0.1:9901/ready 200
# Mark the expected denial so Envoy can omit this probe from its access log.
check_http http://127.0.0.1:8080/ 403 --user-agent code-server-container-healthcheck
