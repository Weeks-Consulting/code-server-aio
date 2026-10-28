#!/bin/sh
# Shared fixtures for the local and S3 backup round trips.
# Callers supply image, prefix, source_volume, app_name, archive_dir, and started_before.
# shellcheck disable=SC2154

start_backup_fixture() {
    docker volume create --label "com.docker.compose.project=$prefix" \
        --label com.docker.compose.volume=code-server-home "$source_volume" >/dev/null
    docker run --rm --network none --mount "type=volume,src=$source_volume,dst=/data" \
        --entrypoint /bin/sh "$image" -ec '
            mkdir -p /data/cloud-user/.config /data/linuxbrew
            echo saved-settings > /data/cloud-user/.config/sentinel
            echo saved-brew > /data/linuxbrew/sentinel
            chown -R 10000:10000 /data
            echo low-owner > /data/low-uid
            chown 1:13 /data/low-uid
        '
    docker run -d --name "$app_name" \
        --label "docker-volume-backup.stop-during-backup=$prefix-home" \
        --mount "type=volume,src=$source_volume,dst=/home" \
        --entrypoint /usr/bin/sleep "$image" infinity >/dev/null
}

assert_backup_restarted_app() {
    test "$(docker inspect -f '{{.State.Running}}' "$app_name")" = true
    test "$(docker inspect -f '{{.State.StartedAt}}' "$app_name")" != "$started_before"
}

assert_restored_home() {
    docker run --rm --network none --mount "type=volume,src=$prefix-restored,dst=/data,readonly" \
        --entrypoint /bin/sh "$image" -ec '
            grep -qx saved-settings /data/cloud-user/.config/sentinel
            grep -qx saved-brew /data/linuxbrew/sentinel
            grep -qx low-owner /data/low-uid
            test "$(stat -c %u:%g /data/cloud-user/.config/sentinel)" = 10000:10000
            test "$(stat -c %u:%g /data/low-uid)" = 1:13
        '
}

remove_test_archives() {
    docker run --rm --network none --mount "type=bind,src=$archive_dir,dst=/test-archives" \
        --entrypoint /bin/sh "$image" -ec 'find /test-archives -mindepth 1 -delete' >/dev/null
    rmdir "$archive_dir"
}
