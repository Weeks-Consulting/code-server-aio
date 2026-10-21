#!/bin/bash
# Download act temporarily and run this repository's native-architecture CI tests.
set -euo pipefail

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
case "$(uname -m)" in
    x86_64) asset_arch=x86_64; matrix_arch=amd64; runner=ubuntu-24.04 ;;
    aarch64|arm64) asset_arch=arm64; matrix_arch=arm64; runner=ubuntu-24.04-arm ;;
    *) echo 'Unsupported architecture: use an amd64 or arm64 Linux host.' >&2; exit 1 ;;
esac
if [[ $(uname -s) != Linux ]]; then
    echo 'This script requires Linux.' >&2
    exit 1
fi

act_dir=$(mktemp -d)
trap 'rm -rf "$act_dir"' EXIT
curl --fail --silent --show-error --location --retry 3 \
    "https://github.com/nektos/act/releases/latest/download/act_Linux_${asset_arch}.tar.gz" \
    --output "$act_dir/act.tar.gz"
tar -xzf "$act_dir/act.tar.gz" -C "$act_dir" act

cd "$project_dir"
"$act_dir/act" pull_request \
    --workflows .github/workflows/container.yaml \
    --job test \
    --matrix "arch:$matrix_arch" \
    -P "$runner=catthehacker/ubuntu:act-24.04" \
    "$@"
