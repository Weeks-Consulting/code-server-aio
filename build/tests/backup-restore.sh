#!/bin/sh
# Exercise Offen archive creation and our restore command without S3 credentials.
set -eu
image=${1:?Pass the application image}
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=build/tests/backup-common.sh
. "$script_dir/backup-common.sh"
restore_script="$script_dir/../tools/restore-home.sh"
project_dir=$(cd -- "$script_dir/../.." && pwd)
prefix="code-server-restore-$$"
source_volume="${prefix}_code-server-home"
app_name="$prefix-app"
# The checkout must be shared with Docker on macOS; system temp paths may not be.
archive_dir=$(mktemp -d "${TEST_TMPDIR:-$project_dir}/.test-tmp.XXXXXXXX")
assert_metadata() {
    python3 - "$1" "$2" <<'PYTHON'
import os, stat, sys
info = os.stat(sys.argv[1])
assert (info.st_uid, info.st_gid) == (os.getuid(), os.getgid()), sys.argv[1]
assert stat.S_IMODE(info.st_mode) == int(sys.argv[2], 8), sys.argv[1]
PYTHON
}
command -v gpg >/dev/null || { echo 'Install gpg to run encrypted backup/restore tests.' >&2; exit 1; }
# Generate a disposable key through the same initializer as deployed installations.
touch "$archive_dir/.env"
python3 "$script_dir/../tools/backup-key.py" "$archive_dir/.env"
BACKUP_GPG_PASSPHRASE=$(sed -n 's/^BACKUP_GPG_PASSPHRASE=//p' "$archive_dir/.env")
export BACKUP_GPG_PASSPHRASE
BACKUP_ID=$prefix
export BACKUP_ID
cleanup() {
    docker rm -f "$app_name" >/dev/null 2>&1 || true
    docker volume rm "$source_volume" "${prefix}_docker-data" "$prefix-restored" "$prefix-relative" >/dev/null 2>&1 || true
    docker network rm "${prefix}_default" >/dev/null 2>&1 || true
    remove_test_archives
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Retention prunes only this deployment's prefix, leaving unrelated files alone.
old_archive="$archive_dir/$prefix-home-2000-01-01T00-00-00.tar.gz.gpg"
printf 'old archive' > "$old_archive"
printf 'unrelated' > "$archive_dir/unrelated.txt"
python3 - "$old_archive" "$archive_dir/unrelated.txt" <<'PY'
import os, sys
for name in sys.argv[1:]:
    os.utime(name, (946684800, 946684800))
PY
# A lightweight application verifies backup stop/restart behavior.
start_backup_fixture
started_before=$(docker inspect -f '{{.State.StartedAt}}' "$app_name")
BACKUP_LOCAL_UID=$(id -u) BACKUP_LOCAL_GID=$(id -g) BACKUP_LOCAL_PATH="$archive_dir" docker compose --project-directory "$project_dir" \
    --project-name "$prefix" --env-file "$project_dir/.env.example" \
    -f "$project_dir/compose.yaml" -f "$project_dir/compose.backup-common.yaml" -f "$project_dir/compose.backup-local.yaml" \
    run --rm --no-deps --entrypoint backup backup
assert_backup_restarted_app
test ! -e "$old_archive"
test -f "$archive_dir/unrelated.txt"
echo 'PASS: Local backup stopped and restarted the labeled application.'
archive=$(find "$archive_dir" -maxdepth 1 -name '*.tar.gz.gpg' -print -quit)
test -n "$archive"
assert_metadata "$archive" 600
assert_metadata "$archive_dir" 700
echo 'PASS: Local archive belongs to the invoking user with mode 600.'
mv "$archive" "$archive_dir/restore-test.tar.gz.gpg"
if tar -tzf "$archive_dir/restore-test.tar.gz.gpg" 2>/dev/null; then
    echo 'Offen produced an unencrypted archive.' >&2; exit 1
fi
docker volume create "$prefix-restored" >/dev/null
docker run --rm --network none --mount "type=volume,src=$prefix-restored,dst=/data" \
    --entrypoint /bin/sh "$image" -ec 'echo original > /data/original; ln -s original /data/link; chown 1:13 /data/original'
# A wrong or missing key must fail before changing the original volume.
if BACKUP_GPG_PASSPHRASE=wrong-key RESTORE_IMAGE="$image" sh "$restore_script" "$archive_dir/restore-test.tar.gz.gpg" "$prefix-restored"; then
    echo 'Encrypted restore accepted the wrong key.' >&2; exit 1
fi
if BACKUP_GPG_PASSPHRASE='' RESTORE_IMAGE="$image" sh "$restore_script" "$archive_dir/restore-test.tar.gz.gpg" "$prefix-restored"; then
    echo 'Encrypted restore accepted a missing key.' >&2; exit 1
fi
docker run --rm --network none --mount "type=volume,src=$prefix-restored,dst=/data,readonly" \
    --entrypoint /bin/sh "$image" -ec 'grep -qx original /data/original; test -L /data/link'
# Invalid input must leave neither a rollback directory nor a claimed backup path.
printf 'not a gzip archive' > "$archive_dir/corrupt.tar.gz"
if RESTORE_IMAGE="$image" sh "$restore_script" "$archive_dir/corrupt.tar.gz" "$prefix-restored" > "$archive_dir/failed-restore.log" 2>&1; then
    echo 'Restore accepted a corrupt archive.' >&2; exit 1
fi
test -z "$(find "$archive_dir" -type d -name 'pre-restore.*' -print -quit)"
if grep -q 'Original volume backup:' "$archive_dir/failed-restore.log"; then
    echo 'Failed validation advertised a nonexistent rollback.' >&2; exit 1
fi
docker run --rm --network none --mount "type=volume,src=$prefix-restored,dst=/data,readonly" \
    --entrypoint /bin/sh "$image" -ec 'grep -qx original /data/original; test -L /data/link'
echo 'PASS: Failed validation leaves no rollback directory or misleading path.'
# Exercise installed-launcher dotenv parsing without an inherited key.
cp "$project_dir/manage" "$archive_dir/manage"
cp "$project_dir/manage.py" "$archive_dir/manage.py"
cp "$restore_script" "$archive_dir/restore-home.sh"
# act shares a long TMPDIR with sibling containers. GPG agent sockets must still
# fit even when the caller's temporary directory exceeds the Unix socket limit.
restore_tmp="$archive_dir/restore-temporary-directory-with-a-long-path-for-agent-socket-coverage"
mkdir "$restore_tmp"
env -u BACKUP_GPG_PASSPHRASE TMPDIR="$restore_tmp" RESTORE_IMAGE="$image" sh "$archive_dir/manage" restore \
    --archive "$archive_dir/restore-test.tar.gz.gpg" --volume "$prefix-restored"
assert_restored_home
rollback=$(find "$archive_dir" -name original-home.tar.gz.gpg -print -quit)
test -n "$rollback"
assert_metadata "$rollback" 600
assert_metadata "$(dirname -- "$rollback")" 700
# The caller can read rollback archives without another privileged host command.
printf '%s\n' "$BACKUP_GPG_PASSPHRASE" | gpg --batch --pinentry-mode loopback --passphrase-fd 0 \
    --output "$archive_dir/rollback-check.tar.gz" --decrypt "$rollback"
tar -tzf "$archive_dir/rollback-check.tar.gz" >/dev/null
echo 'PASS: Rollback archive belongs to the effective caller with mode 600.'
docker run --rm --network none --mount "type=bind,src=$archive_dir/rollback-check.tar.gz,dst=/rollback.tar.gz,readonly" \
    --entrypoint /bin/sh "$image" -ec '
        mkdir /verify
        tar --numeric-owner -xzf /rollback.tar.gz -C /verify
        grep -qx original /verify/backup/code-server-home/original
        test "$(readlink /verify/backup/code-server-home/link)" = original
    '
docker run --rm --network none --mount "type=volume,src=$prefix-restored,dst=/data,readonly" \
    --entrypoint /bin/sh "$image" -ec 'test ! -e /data/original; test "$(stat -c %u:%g /data/low-uid)" = 1:13'
# The rollback archive uses the same format and can restore the original contents.
RESTORE_IMAGE="$image" sh "$restore_script" "$rollback" "$prefix-restored"
docker run --rm --network none --mount "type=volume,src=$prefix-restored,dst=/data,readonly" \
    --entrypoint /bin/sh "$image" -ec 'grep -qx original /data/original; test "$(stat -c %u:%g /data/original)" = 1:13; test -L /data/link'
test -z "$(find "$archive_dir" -mindepth 2 -type d -name 'pre-restore.*' -print -quit)"
test "$(find "$archive_dir" -maxdepth 1 -type d -name 'pre-restore.*' | wc -l)" -eq 2
echo 'PASS: In-place restore preserved numeric owners and created a usable rollback archive.'
docker run --rm --network none --mount "type=volume,src=$source_volume,dst=/backup/code-server-home,readonly" \
    --mount "type=bind,src=$archive_dir,dst=/archive" --entrypoint /bin/sh "$image" \
    -ec 'tar -C / -czf /archive/relative.tar.gz backup/code-server-home'
docker volume create "$prefix-relative" >/dev/null
BACKUP_GPG_PASSPHRASE='' RESTORE_IMAGE="$image" sh "$restore_script" "$archive_dir/relative.tar.gz" "$prefix-relative"
docker run --rm --network none --mount "type=volume,src=$prefix-relative,dst=/data,readonly" \
    --entrypoint /bin/sh "$image" -ec 'grep -qx saved-settings /data/cloud-user/.config/sentinel'
echo 'Backup/restore round trip passed.'
python3 "$script_dir/integration_restore.py" "$image"
