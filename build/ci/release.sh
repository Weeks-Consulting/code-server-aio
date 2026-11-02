#!/usr/bin/env bash
# Release CI helpers. Each subcommand runs in a separate workflow step.
set -euo pipefail

# Select the run's publication mode; redirect latest-release rebuilds to a run on the tag.
select_source() (
    tag=$RELEASE_TAG
    publish=false
    dispatched=false
    if [[ "$EVENT_NAME" == schedule || ( "$EVENT_NAME" == workflow_dispatch && "$PUBLISH_LATEST" == true ) ]]; then
        tag=$(gh api "repos/$GH_REPO/releases/latest" --jq .tag_name)
        [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[1-9][0-9]*)?$ ]] || {
            echo "Latest stable release must use a vX.Y.Z or vX.Y.Z-N tag (N >= 1); got: $tag" >&2
            exit 1
        }
        if [[ "$SOURCE_REF" == "refs/tags/$tag" ]]; then
            publish=true
        else
            # Provenance records the run's own ref, so rebuild in a run started on the tag.
            workflow=${WORKFLOW_REF%@*}
            gh workflow run "${workflow##*/}" --ref "$tag" -f publish_latest=true
            printf 'Dispatched rebuild of %s from its tag\n' "$tag" >> "$GITHUB_STEP_SUMMARY"
            dispatched=true
            tag=
        fi
    elif [[ "$EVENT_NAME" == release ]]; then
        publish=true
    fi
    printf 'tag=%s\npublish=%s\ndispatched=%s\n' "$tag" "$publish" "$dispatched" >> "$GITHUB_OUTPUT"
)

# Validate the pinned code-server version and optional release tag.
release_version() (
    python3 - "$(dirname -- "${BASH_SOURCE[0]}")/../.." <<'PYTHON'
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(sys.argv[1]).resolve()

version = json.loads((ROOT / "build/versions.json").read_text())["packages"]["code-server"]["version"]
if not re.fullmatch(r"\d+\.\d+\.\d+", version):
    raise SystemExit("build/versions.json must contain a version such as 4.141.0")
expected = "v" + version
tag = os.environ.get("RELEASE_TAG", "")
if tag and not re.fullmatch(re.escape(expected) + r"(?:-[1-9][0-9]*)?", tag):
    raise SystemExit(f"Release tag must be {expected} or {expected}-N (N >= 1) to match build/versions.json (got {tag})")
print(f"Building code-server {version}" + (f" for release {tag}" if tag else ""))
with open(os.environ["GITHUB_OUTPUT"], "a") as output:
    output.write(f"version={version}\n")
PYTHON
)

# Push the exact tested per-architecture images by digest and stage their index.
# Usage: release.sh stage-images IMAGE ARTIFACT_DIR; ARTIFACT_DIR holds tested-image-<arch>/.
stage_images() (
    image=$1
    artifacts=$2
    staging=$(mktemp -d)
    trap 'rm -rf -- "$staging"' EXIT
    printf '%s' "$GH_TOKEN" | skopeo login --authfile "$staging/auth.json" \
        ghcr.io -u "$REGISTRY_USER" --password-stdin

    sources=()
    for arch in amd64 arm64; do
        tested="$artifacts/tested-image-$arch"
        image_digest=$(cat "$tested/digest")
        [[ "$image_digest" =~ ^sha256:[a-f0-9]{64}$ ]]
        [[ "$(skopeo manifest-digest "$tested/image/manifest.json")" == "$image_digest" ]]
        skopeo copy --preserve-digests --authfile "$staging/auth.json" \
            "dir:$tested/image" "docker://$image@$image_digest"
        sources+=("$image@$image_digest")
    done

    # Keep the index under the shared staging tag until it is attested.
    docker buildx imagetools create --metadata-file "$staging/metadata.json" \
        --tag "$image:staging" "${sources[@]}"
    image_digest=$(jq -r '."containerimage.descriptor".digest' "$staging/metadata.json")
    [[ "$image_digest" =~ ^sha256:[a-f0-9]{64}$ ]]
    echo "digest=$image_digest" >> "$GITHUB_OUTPUT"
)

# Tag the attested index; only the current stable release moves `latest`.
publish_tags() (
    : "${IMAGE:?IMAGE must be set by the publishing workflow}"
    args=(--tag "$IMAGE:${RELEASE_TAG#v}")
    if [[ "$PRERELEASE" != true ]]; then
        latest=$(gh api "repos/$GH_REPO/releases/latest" --jq .tag_name)
        if [[ "$latest" == "$RELEASE_TAG" ]]; then
            args+=(--tag "$IMAGE:latest")
        fi
    fi
    docker buildx imagetools create "${args[@]}" "$IMAGE@$DIGEST"
    printf 'Published attested image: %s@%s\n' "$IMAGE" "$DIGEST" >> "$GITHUB_STEP_SUMMARY"
)

case "${1:-}" in
    select-source|release-version|publish-tags)
        command=${1//-/_}
        shift
        "$command" "$@"
        ;;
    stage-images)
        if [[ $# != 3 ]]; then
            echo "Usage: $0 stage-images IMAGE ARTIFACT_DIR" >&2
            exit 1
        fi
        shift
        stage_images "$@"
        ;;
    *)
        echo "Usage: $0 {select-source|release-version|stage-images IMAGE ARTIFACT_DIR|publish-tags}" >&2
        exit 1
        ;;
esac
