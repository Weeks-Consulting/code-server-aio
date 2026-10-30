#!/usr/bin/env bash
# Select the run's publication mode; redirect latest-release rebuilds to a run on the tag.
set -euo pipefail

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
