#!/bin/sh
# Exercise the production S3 overlay against a disposable RustFS bucket.
set -eu
image=${1:?Pass the application image}
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=build/tests/backup-common.sh
. "$script_dir/backup-common.sh"
project_dir=$(cd -- "$script_dir/../.." && pwd)
prefix="code-server-s3-$$"
source_volume="${prefix}_code-server-home"
app_name="$prefix-app"
archive_dir=$(mktemp -d "${TEST_TMPDIR:-$project_dir}/.test-tmp.XXXXXXXX")
# Override deployment settings, including optional prefix/name settings inherited
# from the caller. No real credentials or endpoints are used.
BACKUP_AWS_ACCESS_KEY_ID=s3-test-access
BACKUP_AWS_SECRET_ACCESS_KEY=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
BACKUP_GPG_PASSPHRASE=$(python3 -c 'import secrets; print(secrets.token_hex(32))')
BACKUP_AWS_S3_BUCKET_NAME=backup-test
BACKUP_AWS_ENDPOINT=rustfs:9000
BACKUP_AWS_S3_BUCKET_LOOKUP=path
BACKUP_AWS_S3_PATH="$prefix/home"
BACKUP_ID=$prefix
export BACKUP_AWS_ACCESS_KEY_ID BACKUP_AWS_SECRET_ACCESS_KEY BACKUP_GPG_PASSPHRASE
export BACKUP_AWS_S3_BUCKET_NAME BACKUP_AWS_ENDPOINT BACKUP_AWS_S3_BUCKET_LOOKUP BACKUP_AWS_S3_PATH BACKUP_ID
compose() {
    docker compose --project-directory "$project_dir" --project-name "$prefix" \
        --env-file "$project_dir/.env.example" -f "$project_dir/compose.yaml" \
        -f "$project_dir/compose.backup-common.yaml" -f "$project_dir/compose.backup.yaml" -f "$script_dir/compose.backup-s3.yaml" "$@"
}
cleanup() {
    result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then compose logs rustfs >&2 || true; fi
    docker rm -f "$app_name" >/dev/null 2>&1 || true
    compose down --volumes --remove-orphans >/dev/null 2>&1 || true
    docker volume rm "$source_volume" "$prefix-restored" >/dev/null 2>&1 || true
    remove_test_archives
    exit "$result"
}
trap cleanup EXIT
s3() {
    docker run --rm --network "${prefix}_default" \
        -e BACKUP_AWS_ACCESS_KEY_ID -e BACKUP_AWS_SECRET_ACCESS_KEY --entrypoint /bin/sh "$image" -ec '
        exec curl --silent --show-error --fail --connect-timeout 3 --max-time 10 \
            --aws-sigv4 aws:amz:us-east-1:s3 \
            --user "$BACKUP_AWS_ACCESS_KEY_ID:$BACKUP_AWS_SECRET_ACCESS_KEY" "$@"
        ' sh "$@"
}
compose up -d rustfs
# Bounded readiness check uses the same signed request as the rest of the test.
attempt=0
until s3 "http://rustfs:9000/" > /dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 30 ]; then echo 'RustFS did not become ready.' >&2; exit 1; fi
    sleep 1
done
s3 -X PUT "http://rustfs:9000/$BACKUP_AWS_S3_BUCKET_NAME"
# A lightweight application verifies backup stop/restart behavior.
start_backup_fixture
started_before=$(docker inspect -f '{{.State.StartedAt}}' "$app_name")
compose run --rm --no-deps --entrypoint backup backup
assert_backup_restarted_app
echo 'PASS: S3 backup stopped and restarted the labeled application.'
# List the whole bucket so an upload to an unexpected prefix also fails.
s3 "http://rustfs:9000/$BACKUP_AWS_S3_BUCKET_NAME?list-type=2" > "$archive_dir/objects.xml"
key=$(python3 - "$archive_dir/objects.xml" "$prefix" <<'PYTHON'
import re, sys
import xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
assert root.findtext("s3:IsTruncated", namespaces=ns) == "false"
keys = [entry.text for entry in root.findall("s3:Contents/s3:Key", ns)]
assert len(keys) == 1, keys
project = re.escape(sys.argv[2])
assert re.fullmatch(project + r"/home/" + project + r"-home-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.tar\.gz\.gpg", keys[0]), keys
print(keys[0])
PYTHON
)
s3 "http://rustfs:9000/$BACKUP_AWS_S3_BUCKET_NAME/$key" > "$archive_dir/backup.tar.gz.gpg"
test -s "$archive_dir/backup.tar.gz.gpg"
if tar -tzf "$archive_dir/backup.tar.gz.gpg" >/dev/null 2>&1; then
    echo 'S3 backup is not encrypted.' >&2; exit 1
fi
echo 'PASS: One encrypted archive uploaded under the expected S3 prefix.'
docker volume create "$prefix-restored" >/dev/null
RESTORE_IMAGE="$image" sh "$script_dir/../tools/restore-home.sh" \
    "$archive_dir/backup.tar.gz.gpg" "$prefix-restored"
assert_restored_home
echo 'PASS: S3 backup/restore round trip preserved contents and numeric owners.'
