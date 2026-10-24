set shell := ["sh", "-eu", "-c"]

image := "code-server-cloudflare:test"

# List local development commands.
default:
    @just --list

# Install host build/test dependencies with Homebrew or Debian apt.
install:
    sh build/tools/install-deps.sh

# Build with BuildKit and load the image into Docker for the integration tests.
build:
    docker buildx build --load -f build/Dockerfile -t {{quote(image)}} .

# Check shell syntax and all Compose file combinations without using .env.
check:
    sh build/tests/check.sh

# Build once and run all local checks and integration tests.
test: check test-proxy test-smoke test-backup

# Test JWT validation, key rotation, error pages, and WebSocket reloads.
test-proxy: build
    sh build/tests/proxy.sh {{quote(image)}}

# Test startup, Homebrew persistence, health, and service recovery.
test-smoke: build
    sh build/tests/smoke.sh {{quote(image)}}

# Test backup and restore using temporary volumes and local archives.
test-backup: build
    sh build/tests/backup-restore.sh {{quote(image)}}

# Run the native-architecture GitHub Actions test job with temporary act.
actions:
    sh build/tests/actions.sh

# List GitHub Actions jobs without running them.
actions-list:
    sh build/tests/actions.sh --list

# Print repository dependency versions without hashes (offline).
[positional-arguments]
version *args:
    python3 build/tools/dependencies.py version "$@"

# Look up upstream versions and confirm before updating pins and checksums.
[positional-arguments]
upgrade *args:
    python3 build/tools/dependencies.py upgrade "$@"

# Check upstream dependency versions without prompting or changing files.
[positional-arguments]
outdated *args:
    python3 build/tools/dependencies.py outdated "$@"
