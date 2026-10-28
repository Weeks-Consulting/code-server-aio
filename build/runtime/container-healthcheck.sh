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
    code=$(curl --unix-socket /run/code-server/backend.sock --noproxy '*' --silent --show-error --location --max-time 2 \
        --output /dev/null --write-out '%{http_code}' "$url") \
        || not_ready "Request to $url failed"
    [[ "$code" == "$expected" ]] \
        || not_ready "$url returned $code, expected $expected"
}

check_http http://localhost/healthz 200

for endpoint in '9901 /ready 200' '8080 / 403'; do
    read -r port path expected <<< "$endpoint"
    code=$(curl --noproxy '*' --silent --show-error --max-time 2 --output /dev/null \
        --write-out '%{http_code}' "http://127.0.0.1:$port$path") || not_ready "Envoy request failed"
    [[ "$code" == "$expected" ]] || not_ready "Envoy $path returned $code, expected $expected"
done
