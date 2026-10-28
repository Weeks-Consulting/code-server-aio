#!/bin/sh
# Exercise the Envoy executable and template bundled in IMAGE.
set -eu
image=${1:?Pass the image to test}
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
docker run --rm --network none --entrypoint /usr/bin/python3 \
    --mount "type=bind,src=$script_dir/test_access_proxy.py,dst=/tmp/test_access_proxy.py,readonly" \
    "$image" /tmp/test_access_proxy.py
python3 "$script_dir/test_isolation.py" "$image"
