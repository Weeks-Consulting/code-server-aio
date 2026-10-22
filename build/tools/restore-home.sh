#!/bin/bash
# Restore a downloaded archive into a new, empty Docker volume.
set -euo pipefail
archive=${1:?Usage: bash build/tools/restore-home.sh ARCHIVE.tar.gz EMPTY_VOLUME}
volume=${2:?Pass a new or empty target volume}
if [ ! -f "$archive" ]; then
    echo "Archive does not exist or is not a regular file: $archive" >&2; exit 1
fi
archive=$(realpath -- "$archive")
case "$archive,$volume" in
    *,*,*) echo "Archive paths and volume names must not contain commas" >&2; exit 1 ;;
esac
docker run --rm --network none \
    --mount "type=bind,src=$archive,dst=/backup.tar.gz,readonly" \
    --mount "type=volume,src=$volume,dst=/restore" \
    --entrypoint /bin/bash "${RESTORE_IMAGE:-debian:13-slim}" -ec '
        if [ -n "$(find /restore -mindepth 1 -maxdepth 1 -print -quit)" ]; then
            echo "Refusing to restore into a non-empty volume" >&2; exit 1
        fi
        # Offen currently records absolute names; older archives may be relative.
        if tar --list --gzip --file /backup.tar.gz | grep -Fx /backup/code-server-home >/dev/null; then
            member=/backup/code-server-home
        else
            member=backup/code-server-home
        fi
        tar --extract --gzip --file /backup.tar.gz --directory /restore \
            --strip-components=2 "$member"
    '
