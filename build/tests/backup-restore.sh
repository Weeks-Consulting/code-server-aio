#!/bin/bash
# Exercise Offen archive creation and our restore command without S3 credentials.
set -euo pipefail
image=${1:?Pass the application image}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
restore_script="$script_dir/../tools/restore-home.sh"
project_dir=$(cd -- "$script_dir/../.." && pwd)
prefix="code-server-restore-$$"
source_volume="${prefix}_code-server-home"
app_name="$prefix-app"
archive_dir=$(mktemp -d)
cleanup() {
    docker rm -f "$app_name" >/dev/null 2>&1 || true
    docker volume rm "$source_volume" "${prefix}_docker-data" "$prefix-restored" "$prefix-relative" >/dev/null 2>&1 || true
    docker network rm "${prefix}_default" >/dev/null 2>&1 || true
    docker run --rm --network none --mount "type=bind,src=$archive_dir,dst=/test-archives" \
        --entrypoint /bin/bash "$image" -ec 'find /test-archives -mindepth 1 -delete' >/dev/null
    rmdir "$archive_dir"
}
trap cleanup EXIT
docker volume create --label "com.docker.compose.project=$prefix" \
    --label com.docker.compose.volume=code-server-home "$source_volume" >/dev/null
docker run --rm --network none --mount "type=volume,src=$source_volume,dst=/data" \
    --entrypoint /bin/bash "$image" -ec '
        mkdir -p /data/cloud-user/.config /data/linuxbrew
        echo saved-settings > /data/cloud-user/.config/sentinel
        echo saved-brew > /data/linuxbrew/sentinel
        chown -R 10000:10000 /data
    '
# Exercise the actual local Compose overlay, with no S3 settings in the container.
# A lightweight stand-in verifies the labeled application is stopped and restarted.
docker run -d --name "$app_name" \
    --label "docker-volume-backup.stop-during-backup=$prefix-home" \
    --mount "type=volume,src=$source_volume,dst=/home" \
    --entrypoint /usr/bin/sleep "$image" infinity >/dev/null
started_before=$(docker inspect -f '{{.State.StartedAt}}' "$app_name")
BACKUP_LOCAL_PATH="$archive_dir" docker compose --project-directory "$project_dir" \
    --project-name "$prefix" --env-file "$project_dir/.env.example" \
    -f "$project_dir/compose.yaml" -f "$project_dir/compose.backup-local.yaml" \
    run --rm --no-deps --entrypoint backup backup
test "$(docker inspect -f '{{.State.Running}}' "$app_name")" = true
test "$(docker inspect -f '{{.State.StartedAt}}' "$app_name")" != "$started_before"
echo 'PASS: Local backup stopped and restarted the labeled application.'
archive=$(find "$archive_dir" -maxdepth 1 -name '*.tar.gz' -print -quit)
test -n "$archive"
mv "$archive" "$archive_dir/restore-test.tar.gz"
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
docker run --rm --network none --mount "type=volume,src=$source_volume,dst=/backup/code-server-home,readonly" \
    --mount "type=bind,src=$archive_dir,dst=/archive" --entrypoint /bin/bash "$image" \
    -ec 'tar -C / -czf /archive/relative.tar.gz backup/code-server-home'
RESTORE_IMAGE="$image" bash "$restore_script" "$archive_dir/relative.tar.gz" "$prefix-relative"
docker run --rm --network none --mount "type=volume,src=$prefix-relative,dst=/data,readonly" \
    --entrypoint /bin/bash "$image" -ec 'grep -qx saved-settings /data/cloud-user/.config/sentinel'
echo 'Backup/restore round trip passed.'
