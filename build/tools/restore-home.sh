#!/bin/sh
# Back up and restore an existing, stopped deployment's home volume in place.
set -eu
archive=${1:?Usage: sh restore-home.sh ARCHIVE.tar.gz[.gpg] EXISTING_VOLUME}
volume=${2:?Pass the original deployment home volume}
if [ ! -f "$archive" ]; then
    echo "Archive does not exist or is not a regular file: $archive" >&2; exit 1
fi
command -v python3 >/dev/null || { echo 'Install python3 to restore backups.' >&2; exit 1; }
archive=$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$archive")
case "$archive,$volume" in
    *,*,*) echo "Archive paths and volume names must not contain commas" >&2; exit 1 ;;
esac
decryption_directory=
rollback_directory=
cleanup() {
    if [ -n "$decryption_directory" ]; then rm -rf -- "$decryption_directory"; fi
    # Remove only an empty directory. A verified rollback must survive failures.
    if [ -n "$rollback_directory" ]; then rmdir -- "$rollback_directory" 2>/dev/null || true; fi
}
trap cleanup EXIT

# Decrypt into a private temporary directory before any volume changes.
# Keep the key out of argv and remove plaintext on success or failure.
case "$archive" in *.gpg)
    [ -n "${GPG_PASSPHRASE:-}" ] || {
        echo 'Encrypted restore requires the original GPG_PASSPHRASE (manage reads it from .env).' >&2; exit 1;
    }
    command -v gpg >/dev/null || { echo 'Install gpg on the Docker host to restore encrypted backups.' >&2; exit 1; }
    encrypted_archive=$archive
    # Stay beside the archive so Docker can access the plaintext on macOS too.
    decryption_directory=$(mktemp -d "$(dirname -- "$archive")/.decrypt.XXXXXXXX")
    chmod 700 "$decryption_directory"
    gpg --homedir "$decryption_directory" --batch --pinentry-mode loopback --passphrase-fd 3 \
        --output "$decryption_directory/home.tar.gz" --decrypt "$archive" 3<<PASSPHRASE
$GPG_PASSPHRASE
PASSPHRASE
    chmod 600 "$decryption_directory/home.tar.gz"
    archive="$decryption_directory/home.tar.gz"
;;
esac
# Inspect first: Docker's volume mount would otherwise create a misspelled volume.
docker volume inspect "$volume" >/dev/null
running_containers=$(docker ps -q --filter "volume=$volume")
if [ -n "$running_containers" ]; then
    echo "Stop all containers using $volume before restoring (use docker compose down)." >&2
    exit 1
fi
# Archive ownership follows the effective caller; owners inside the volume are
# restored independently by a root helper. Never infer identity from sudo variables.
archive_uid=$(id -u)
archive_gid=$(id -g)
case "$archive_uid:$archive_gid" in
    *[!0-9:]*|:*|*:) echo 'Backup UID and GID must be numeric.' >&2; exit 1 ;;
esac
rollback_source=${encrypted_archive:-$archive}
rollback_parent=$(dirname -- "$rollback_source")
case "${rollback_source##*/}" in
    original-home.tar.gz|original-home.tar.gz.gpg)
        # Also flatten rollback directories produced by older helper versions.
        while printf '%s\n' "${rollback_parent##*/}" | LC_ALL=C grep -Eq '^pre-restore\.[[:alnum:]]{8}$'; do
            rollback_parent=$(dirname -- "$rollback_parent")
        done
        ;;
esac
rollback_directory=$(mktemp -d "$rollback_parent/pre-restore.XXXXXXXX")
docker run --rm --network none --user 0:0 \
    --env "ARCHIVE_UID=$archive_uid" --env "ARCHIVE_GID=$archive_gid" \
    --env "ROLLBACK_ARCHIVE_PATH=$rollback_directory/original-home.tar.gz" \
    --mount "type=bind,src=$archive,dst=/backup.tar.gz,readonly" \
    --mount "type=bind,src=$rollback_directory,dst=/rollback" \
    --mount "type=volume,src=$volume,dst=/backup/code-server-home,readonly" \
    --mount "type=volume,src=$volume,dst=/restore" \
    --entrypoint /bin/sh "${RESTORE_IMAGE:-debian:13-slim}" -euc '
        umask 077
        # Incomplete archives are never presented as usable rollback backups.
        trap "rm -f -- /rollback/.original-home.tar.gz.partial" EXIT
        # Fully validate and extract before touching the original volume.
        # Offen currently records absolute names; older archives may be relative.
        tar --list --gzip --file /backup.tar.gz > /tmp/members
        if grep -Fx /backup/code-server-home /tmp/members >/dev/null; then
            member=/backup/code-server-home
        else
            member=backup/code-server-home
        fi
        mkdir /staged
        tar --extract --gzip --numeric-owner --same-owner --file /backup.tar.gz \
            --directory /staged --strip-components=2 "$member"
        # Keep the same layout as scheduled backups so this archive can be restored too.
        tar --create --gzip --numeric-owner --file /rollback/.original-home.tar.gz.partial \
            --directory / backup/code-server-home
        tar --list --gzip --file /rollback/.original-home.tar.gz.partial >/dev/null
        chown "$ARCHIVE_UID:$ARCHIVE_GID" /rollback/.original-home.tar.gz.partial
        chmod 600 /rollback/.original-home.tar.gz.partial
        mv -- /rollback/.original-home.tar.gz.partial /rollback/original-home.tar.gz
        printf "Original volume backup: %s\n" "$ROLLBACK_ARCHIVE_PATH"
        echo "Original volume backup complete; replacing volume contents."
        find /restore -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
        cp -a /staged/. /restore/
    '
printf 'Restored %s. Restart the deployment with its usual launch options.\n' "$volume"
