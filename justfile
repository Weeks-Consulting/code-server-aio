set shell := ["bash", "-euo", "pipefail", "-c"]

image := "code-server-cloudflare:test"

# List local development commands.
default:
    @just --list

# Build the local test image.
build:
    docker build -f build/Dockerfile -t {{quote(image)}} .

# Check shell syntax and all Compose file combinations without using .env.
check:
    bash build/tests/check.sh

# Build once and run all local checks and integration tests.
test: check test-proxy test-smoke test-backup

# Test JWT validation, key rotation, error pages, and WebSocket reloads.
test-proxy: build
    bash build/tests/proxy.sh {{quote(image)}}

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
