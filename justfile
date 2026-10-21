set shell := ["bash", "-euo", "pipefail", "-c"]

image := "code-server-cloudflare:test"

# List local development commands.
default:
    @just --list

# Build the local test image.
build:
    docker compose -f compose.yaml -f compose.build.yaml build code-server
    docker tag code-server-cloudflare:local {{quote(image)}}

# Check shell syntax and all Compose file combinations without using .env.
check:
    bash -n build/startup/entrypoint.sh build/backup/restore-home.sh build/tests/*.sh
    sh -n build/homebrew/homebrew.sh
    docker compose --env-file .env.example config --quiet
    docker compose --env-file .env.example -f compose.yaml -f compose.build.yaml config --quiet
    docker compose --env-file .env.example -f compose.yaml -f compose.backup.yaml config --quiet
    docker compose --env-file .env.example -f compose.yaml -f compose.build.yaml -f compose.backup.yaml config --quiet

# Build once and run all local checks and integration tests.
test: check test-proxy test-smoke test-backup

# Test JWT validation, key rotation, error pages, and WebSocket reloads.
test-proxy: build
    docker run --rm --entrypoint /usr/bin/python3 \
        --mount "type=bind,src=$PWD/build/tests/test_access_proxy.py,dst=/tmp/test_access_proxy.py,readonly" \
        {{quote(image)}} /tmp/test_access_proxy.py

# Test startup, Homebrew persistence, health, and service recovery.
test-smoke: build
    bash build/tests/smoke.sh {{quote(image)}}

# Test backup and restore using temporary volumes and local archives.
test-backup: build
    bash build/tests/backup-restore.sh {{quote(image)}}

# Run the native-architecture GitHub Actions test job with temporary act.
actions:
    bash build/tests/actions.sh

# List GitHub Actions jobs without running them.
actions-list:
    bash build/tests/actions.sh --list
