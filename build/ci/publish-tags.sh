#!/usr/bin/env bash
# Tag the attested index; only the current stable release moves `latest`.
set -euo pipefail

args=(--tag "$IMAGE:${RELEASE_TAG#v}")
if [[ "$PRERELEASE" != true ]]; then
    latest=$(gh api "repos/$GH_REPO/releases/latest" --jq .tag_name)
    if [[ "$latest" == "$RELEASE_TAG" ]]; then
        args+=(--tag "$IMAGE:latest")
    fi
fi
docker buildx imagetools create "${args[@]}" "$IMAGE@$DIGEST"
printf 'Published attested image: %s@%s\n' "$IMAGE" "$DIGEST" >> "$GITHUB_STEP_SUMMARY"
