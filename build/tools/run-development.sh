#!/bin/sh
set -eu
cd -P -- "$(dirname -- "$0")/../.."
python3 build/tools/dependencies.py check

# Keep development volumes stable across runs from this checkout.
project=$(python3 -c 'import hashlib, os; print("code-server-aio-dev-" + hashlib.sha256(os.getcwdb()).hexdigest()[:24])')
lock=.run-development.lock
if ! mkdir "$lock"; then
    echo 'Cannot acquire .run-development.lock. Check for another just run in this checkout.' >&2
    exit 1
fi

compose() {
    docker compose --project-name "$project" --env-file .env \
        -f compose.yaml -f compose.build.yaml -f compose.dev.yaml "$@"
}

started=false
cleanup() {
    status=$?
    trap - EXIT
    if [ "$started" = true ]; then
        compose down || { cleanup_status=$?; [ "$status" -ne 0 ] || status=$cleanup_status; }
    fi
    rmdir "$lock" || { cleanup_status=$?; [ "$status" -ne 0 ] || status=$cleanup_status; }
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

project_label="label=com.docker.compose.project=$project"
containers=$(docker container ls --all --quiet --filter "$project_label")
networks=$(docker network ls --quiet --filter "$project_label")
if [ -n "$containers" ] || [ -n "$networks" ]; then
    echo "Compose project $project already has containers or networks. Inspect the existing resources before retrying." >&2
    exit 1
fi
compose config --quiet
printf 'Development Compose project: %s\n' "$project"
started=true
compose up --build
