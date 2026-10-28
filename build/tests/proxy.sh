#!/bin/sh
# Exercise the Envoy executable and template bundled in IMAGE.
set -eu
export PYTHONDONTWRITEBYTECODE=1
image=${1:?Pass the image to test}
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
name="code-server-proxy-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
docker run --rm --name "$name" --network none --entrypoint /usr/bin/python3 \
    --mount "type=bind,src=$script_dir/integration_access_proxy.py,dst=/tmp/integration_access_proxy.py,readonly" \
    "$image" /tmp/integration_access_proxy.py
python3 "$script_dir/integration_isolation.py" "$image"
