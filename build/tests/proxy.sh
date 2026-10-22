#!/bin/bash
# Run JWT validation, key rotation, error-page, and WebSocket tests inside IMAGE.
set -euo pipefail
image=${1:?Pass the image to test}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
docker run --rm --network none --entrypoint /usr/bin/python3 \
    --mount "type=bind,src=$script_dir/test_access_proxy.py,dst=/tmp/test_access_proxy.py,readonly" \
    "$image" /tmp/test_access_proxy.py
