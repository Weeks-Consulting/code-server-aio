#!/usr/bin/env bash
# Push the exact tested per-architecture images by digest and stage their index.
# Usage: stage-images.sh IMAGE ARTIFACT_DIR; ARTIFACT_DIR holds tested-image-<arch>/.
set -euo pipefail

image=$1
artifacts=$2
staging=$(mktemp -d)
trap 'rm -rf -- "$staging"' EXIT
printf '%s' "$GH_TOKEN" | skopeo login --authfile "$staging/auth.json" \
    ghcr.io -u "$REGISTRY_USER" --password-stdin

sources=()
for arch in amd64 arm64; do
    tested="$artifacts/tested-image-$arch"
    digest=$(cat "$tested/digest")
    [[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]
    [[ "$(skopeo manifest-digest "$tested/image/manifest.json")" == "$digest" ]]
    skopeo copy --preserve-digests --authfile "$staging/auth.json" \
        "dir:$tested/image" "docker://$image@$digest"
    sources+=("$image@$digest")
done

# Keep the index under the shared staging tag until it is attested.
docker buildx imagetools create --metadata-file "$staging/metadata.json" \
    --tag "$image:staging" "${sources[@]}"
digest=$(jq -r '."containerimage.descriptor".digest' "$staging/metadata.json")
[[ "$digest" =~ ^sha256:[a-f0-9]{64}$ ]]
echo "digest=$digest" >> "$GITHUB_OUTPUT"
