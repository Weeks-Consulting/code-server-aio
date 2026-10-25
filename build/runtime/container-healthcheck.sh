#!/bin/bash
# Readiness: all services running, nested Docker ready, proxy enforcing Access.
set -euo pipefail

not_ready() {
    printf 'Not ready: %s\n' "$1" >&2
    exit 1
}

# supervisorctl exits nonzero unless every program and event listener is RUNNING.
status=$(timeout 3 /usr/bin/supervisorctl status 2>&1) || not_ready $'services not running:\n'"$status"

docker_info=$(timeout 3 /usr/bin/docker info --format '{{.ServerVersion}}' 2>&1) \
    || not_ready "Docker is not ready: $docker_info"

check_http() {
    local url=$1 expected=$2 code
    code=$(curl --noproxy '*' --silent --show-error --location --max-time 2 \
        --output /dev/null --write-out '%{http_code}' "$url") \
        || not_ready "Request to $url failed"
    [[ "$code" == "$expected" ]] \
        || not_ready "$url returned $code, expected $expected"
}

check_http http://127.0.0.1:8081/healthz 200
check_http http://127.0.0.1:8080/ 403
