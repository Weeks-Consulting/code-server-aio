#!/bin/bash
# Exercise Offen archive creation and our restore command without S3 credentials.
set -euo pipefail
image=${1:?Pass the application image}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
restore_script="$script_dir/../backup/restore-home.sh"
prefix="code-server-restore-$$"
archive_dir=$(mktemp -d)
cleanup() {
    docker volume rm "$prefix-source" "$prefix-restored" "$prefix-relative" >/dev/null 2>&1 || true
    docker run --rm --network none --mount "type=bind,src=$archive_dir,dst=/test-archives" \
        --entrypoint /bin/bash "$image" -ec 'find /test-archives -mindepth 1 -delete' >/dev/null
    rmdir "$archive_dir"
}
trap cleanup EXIT
docker run --rm --network none --mount "type=volume,src=$prefix-source,dst=/data" \
    --entrypoint /bin/bash "$image" -ec '
        mkdir -p /data/cloud-user/.config /data/linuxbrew
        echo saved-settings > /data/cloud-user/.config/sentinel
        echo saved-brew > /data/linuxbrew/sentinel
        chown -R 10000:10000 /data
    '
docker run --rm --network none --entrypoint backup \
    --mount "type=volume,src=$prefix-source,dst=/backup/code-server-home,readonly" \
    --mount "type=bind,src=$archive_dir,dst=/archive" \
    -e BACKUP_FILENAME=restore-test.tar.gz offen/docker-volume-backup:v2
tar -tzf "$archive_dir/restore-test.tar.gz"
RESTORE_IMAGE="$image" bash "$restore_script" "$archive_dir/restore-test.tar.gz" "$prefix-restored"
docker run --rm --network none --mount "type=volume,src=$prefix-restored,dst=/data,readonly" \
    --entrypoint /bin/bash "$image" -ec '
        grep -qx saved-settings /data/cloud-user/.config/sentinel
        grep -qx saved-brew /data/linuxbrew/sentinel
        test "$(stat -c %u:%g /data/cloud-user/.config/sentinel)" = 10000:10000
    '
if refusal_output=$(RESTORE_IMAGE="$image" bash "$restore_script" "$archive_dir/restore-test.tar.gz" "$prefix-restored" 2>&1); then
    echo 'Restore overwrote a populated volume' >&2; exit 1
fi
if ! grep -Fxq 'Refusing to restore into a non-empty volume' <<< "$refusal_output"; then
    printf 'Restore failed for an unexpected reason:\n%s\n' "$refusal_output" >&2
    exit 1
fi
echo 'PASS: Restore correctly refused a non-empty volume (expected safety check).'
docker run --rm --network none --mount "type=volume,src=$prefix-source,dst=/backup/code-server-home,readonly" \
    --mount "type=bind,src=$archive_dir,dst=/archive" --entrypoint /bin/bash "$image" \
    -ec 'tar -C / -czf /archive/relative.tar.gz backup/code-server-home'
RESTORE_IMAGE="$image" bash "$restore_script" "$archive_dir/relative.tar.gz" "$prefix-relative"
docker run --rm --network none --mount "type=volume,src=$prefix-relative,dst=/data,readonly" \
    --entrypoint /bin/bash "$image" -ec 'grep -qx saved-settings /data/cloud-user/.config/sentinel'
echo 'Backup/restore round trip passed.'
