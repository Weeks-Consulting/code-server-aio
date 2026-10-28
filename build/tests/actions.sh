#!/bin/sh
# Download act temporarily and run this repository's native-architecture CI tests.
set -eu

project_dir=$(cd -- "$(dirname -- "$0")/../.." && pwd)
case "$(uname -m)" in
    x86_64) asset_arch=x86_64; matrix_arch=amd64 ;;
    aarch64|arm64) asset_arch=arm64; matrix_arch=arm64 ;;
    *) echo 'Unsupported architecture: use an amd64 or arm64 host.' >&2; exit 1 ;;
esac
asset_os=$(uname -s)
case "$asset_os" in
    Linux) home_storage=bind ;;
    Darwin)
        home_storage=volume
        # Explicitly retain the CLI endpoint: act otherwise also uses the
        # container socket override as its host connection fallback.
        if [ -n "${DOCKER_CONTEXT:-}" ] || [ -z "${DOCKER_HOST:-}" ]; then
            DOCKER_HOST=$(docker context inspect --format '{{.Endpoints.docker.Host}}')
            export DOCKER_HOST
        fi
        # Mount paths are resolved inside Docker's Linux VM, where the macOS
        # client socket is not usable. Keep Linux/rootless socket autodetection.
        set -- --container-daemon-socket /var/run/docker.sock "$@"
        ;;
    *) echo 'This script requires Linux or macOS.' >&2; exit 1 ;;
esac

act_version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["packages"]["act"]["version"])' "$project_dir/build/build-versions.json")

act_dir=$(mktemp -d "$project_dir/.test-tmp.XXXXXXXX")
test_volume=
cleanup() {
    rm -rf "$act_dir"
    if [ -n "$test_volume" ]; then docker volume rm "$test_volume" >/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Docker bind sources are resolved by the daemon, outside act's runner container.
# Share temporary test data at the same absolute path in both environments.
mkdir "$act_dir/tmp"
test_tmp="$act_dir/tmp"
temp_mount="$test_tmp:$test_tmp"
curl --fail --silent --show-error --location --retry 3 \
    "https://github.com/nektos/act/releases/download/v${act_version}/act_${asset_os}_${asset_arch}.tar.gz" \
    --output "$act_dir/act.tar.gz"
expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["packages"]["act"]["sha256"][sys.argv[2]])' "$project_dir/build/build-versions.json" "act_${asset_os}_${asset_arch}.tar.gz")
python3 - "$act_dir/act.tar.gz" "$expected" <<'PY'
import hashlib, pathlib, sys
if hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest() != sys.argv[2]:
    sys.exit('act archive checksum mismatch')
PY
tar --no-same-owner -xzf "$act_dir/act.tar.gz" -C "$act_dir" act

# The runner and sibling containers need the same daemon-side temp paths. A
# Linux volume preserves ownership that macOS shared directories cannot.
needs_runner=true
for argument do
    case "$argument" in --list|-l|--help|-h) needs_runner=false ;; esac
done
if [ "$asset_os" = Darwin ] && $needs_runner; then
    test_volume="code-server-act-${act_dir##*.test-tmp.}"
    docker volume create "$test_volume" >/dev/null
    test_tmp=$(docker volume inspect --format '{{.Mountpoint}}' "$test_volume")
    temp_mount="$test_volume:$test_tmp"
fi

cd "$project_dir"
"$act_dir/act" pull_request \
    --workflows .github/workflows/container.yaml \
    --job test \
    --matrix "arch:$matrix_arch" \
    --container-architecture "linux/$matrix_arch" \
    --env-file /dev/null \
    --bind \
    --rm \
    --container-options "--volume=$temp_mount" \
    --env "TMPDIR=$test_tmp" \
    --env "TEST_TMPDIR=$test_tmp" \
    --env "TEST_HOME_STORAGE=$home_storage" \
    --env DOCKER_BUILD_RECORD_UPLOAD=false \
    -P "ubuntu-24.04=catthehacker/ubuntu:act-24.04@sha256:c58e2b364da03b0c804c7d660f2ecbedf2f221a382b9baa0b344b0144780ff43" \
    -P "ubuntu-24.04-arm=catthehacker/ubuntu:act-24.04@sha256:c58e2b364da03b0c804c7d660f2ecbedf2f221a382b9baa0b344b0144780ff43" \
    "$@"
