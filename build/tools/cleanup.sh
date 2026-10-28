#!/bin/sh
# Clean the current Docker daemon and generated checkout artifacts.
set -eu

case "${1:-}" in
    '') [ "$#" -eq 0 ] || { echo 'Usage: cleanup.sh [--all]' >&2; exit 2; } ;;
    --all) [ "$#" -eq 1 ] || { echo 'Usage: cleanup.sh [--all]' >&2; exit 2; } ;;
    *) echo 'Usage: cleanup.sh [--all]' >&2; exit 2 ;;
esac

# Remove containers first so their images and (optionally) volumes can be deleted.
containers=$(docker ps -aq)
if [ -n "$containers" ]; then
    # Docker IDs contain no whitespace; split the list into individual arguments.
    # shellcheck disable=SC2086
    docker rm -f $containers
fi
images=$(docker image ls -aq | sort -u)
if [ -n "$images" ]; then
    # shellcheck disable=SC2086
    docker image rm -f $images
fi
docker builder prune --all --force
if [ "${1:-}" = --all ]; then
    docker volume prune --all --force
fi

cd -- "$(dirname -- "$0")/../.."
# Keep tracked source under build/; only delete generated Python/test artifacts.
find . -type d -name .git -prune -o -type d -name __pycache__ -prune -exec rm -rf {} +
find . -maxdepth 1 -type d -name '.test-tmp.*' -exec rm -rf {} +
