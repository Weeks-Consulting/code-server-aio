#!/bin/sh
# Run Envoy JWT, JWKS rotation/outage, TLS, error-page, and WebSocket tests inside IMAGE.
set -eu
image=${1:?Pass the image to test}
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
# Test the binary from the exact Compose image without adding it to our image.
envoy_image=$(sed -n 's/^    image: \(envoyproxy\/envoy:[^ ]*\)$/\1/p' "$script_dir/../../compose.yaml")
volume="envoy-test-bin-$$"
project_dir=$(cd -- "$script_dir/../.." && pwd)
config_dir=$(mktemp -d "${TEST_TMPDIR:-$project_dir}/.test-tmp.XXXXXXXX")
chmod 755 "$config_dir"
cleanup() {
    docker volume rm "$volume" >/dev/null 2>&1 || true
    rm -rf "$config_dir"
}
trap cleanup EXIT
# Use the production Compose template itself, rather than a second config generator.
CLOUDFLARE_TUNNEL_TOKEN=test CF_ACCESS_TEAM_DOMAIN=test.cloudflareaccess.com \
    CF_ACCESS_AUD=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    docker compose --project-directory "$project_dir" --env-file /dev/null \
    -f "$project_dir/compose.yaml" config --format json | \
    python3 -c 'import json,sys; print(json.load(sys.stdin)["configs"]["envoy-bootstrap"]["content"].replace("$$", "$"))' > "$config_dir/envoy.json"
docker volume create "$volume" >/dev/null
docker run --rm --user 0 --network none --entrypoint /bin/sh \
    --mount "type=volume,src=$volume,dst=/export" "$envoy_image" \
    -ec 'cp /usr/local/bin/envoy /export/envoy; chmod 755 /export/envoy'
docker run --rm --network none --entrypoint /usr/bin/python3 \
    --mount "type=bind,src=$config_dir,dst=/etc/envoy,readonly" \
    --mount "type=volume,src=$volume,dst=/envoy-bin,readonly" \
    --mount "type=bind,src=$script_dir/test_access_proxy.py,dst=/tmp/test_access_proxy.py,readonly" \
    "$image" /tmp/test_access_proxy.py
python3 "$script_dir/test_isolation.py" "$image"
