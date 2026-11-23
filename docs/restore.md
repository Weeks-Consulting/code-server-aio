# Restoring a backup

For local backups, select a `.tar.gz.gpg` archive (or a legacy `.tar.gz` archive) from `BACKUP_LOCAL_PATH`.
For configured S3 backups (including Cloudflare R2), run `./manage backup --list` and copy the
`s3://` URI of the archive you want. Listing uses the saved `BACKUP_AWS_S3_PATH` prefix.
You can also pass the full URI of an older archive elsewhere in the configured bucket.
S3 commands require the AWS CLI on the machine running `manage`. They use the saved backup
credentials and endpoint. The credentials need permission to list the bucket and read the archive.
You can also restore a copy of an archive saved locally.

For encrypted archives, install `gpg` and `python3` on the machine running `manage` (`sudo apt-get install gpg python3`
on Debian, or `brew install gnupg python` on macOS). Put the **original** `BACKUP_GPG_PASSPHRASE` in the deployment's `.env`;
`manage backup --restore` reads it without executing the file. An exported `BACKUP_GPG_PASSPHRASE`
can override it to restore an archive encrypted with an older key. The key must be
exactly 64 lowercase hexadecimal characters. Do not generate a
new key for recovery: it cannot decrypt old backups. On a replacement host, recover
the original key from your password manager first.

The helper streams the source into a private staging directory on the target home volume.
For S3 restores, this means the input archive is never saved as a local file. Local archives can
be anywhere the machine running `manage` can read them. Encrypted archives are decrypted in the
stream. A missing or incorrect key stops restoration before replacing original files.
Legacy plaintext `.tar.gz` archives need no key or host GPG installation unless you choose
to encrypt the rollback.

Restore replaces the contents of the original home volume. The helper first extracts
and validates the requested archive in staging, then creates and verifies a rollback
backup before removing original entries. Staged files move into place on the same
filesystem. Allow free space on the volume for one extracted home tree, plus local space
for the compressed rollback. The helper container runs as root
(`--user 0:0`) to preserve numeric ownership, permissions, and symlinks; the host
command can run as any user with Docker access. Catchable termination stops the restore
container before cleaning staging. If replacement has begun, the helper retains staging
and the verified rollback and prints the staging path and recovery command. Further
interrupts during cleanup are ignored so it can finish. Each cleanup Docker call times out
after 60 seconds.

1. Identify the home volume from the saved configuration. Run this in the deployment
   directory, with `sudo` if you installed with `sudo`:

   ```sh
   python3 -c 'import json; print(json.load(open(".manage.compose.json"))["volumes"]["code-server-home"]["name"])'
   ```

   It prints the volume of the project saved by `manage configure`. Do not use a bare
   `docker compose ps`: without `-p` it can resolve a different deployment's project.
2. Stop the stack with `sh manage stop`, which uses the saved project configuration
   and keeps the containers for `manage start` in step 4. Do not use `docker compose down`:
   it removes the containers, so `manage start` then fails until you rerun
   `manage configure`. Stop any other containers using the home volume.
3. Run the launcher with the actual volume name:

   ```sh
   sh manage backup --restore --archive /absolute/path/to/backup.tar.gz.gpg --volume code-server_code-server-home
   ```

   For S3 or R2, pass the URI from the list command:

   ```sh
   sh manage backup --restore --archive 's3://my-bucket/workspace/home/backup.tar.gz.gpg' --volume code-server_code-server-home
   ```

   Run as the user that installed the deployment, with `sudo` if you installed
   with `sudo`. For local archives, the standalone helper accepts the archive path
   and volume as positional arguments: `sh build/tools/restore-home.sh ARCHIVE VOLUME`
   (installed deployments have `restore-home.sh` beside `manage`). For encrypted
   archives, the standalone helper requires an exported `BACKUP_GPG_PASSPHRASE`; it does
   not read `.env`. When using `sudo`, the launcher reads `.env` as root; to use an
   exported older key on Debian, pass it with `sudo --preserve-env=BACKUP_GPG_PASSPHRASE sh ...`.
4. Restart with `manage start` and verify files, extensions,
   Homebrew, and sign-in. The volume name stays unchanged; no Compose override is needed.

The target volume must already exist and have no running containers attached.
Relative archive paths resolve from your current working directory. Keep the stack
stopped throughout restoration; the helper does not restart it automatically.

The helper prints the rollback archive path after verifying it and before replacing
volume contents. Failed extraction leaves no empty rollback directory. It saves
`original-home.tar.gz.gpg` when `BACKUP_GPG_PASSPHRASE` is available, otherwise
`original-home.tar.gz`, in a new private `pre-restore.*` directory. For local restores
this directory is beside the input archive. For S3 restores it is in the deployment
directory. The rollback uses the supplied key for encryption and has mode `600` inside a
mode `700` directory. Both belong to the effective user running `manage backup --restore`
(or the standalone helper), so that user can read and copy the rollback archive.
If the command runs as root, root owns these files; sudo environment variables are
ignored. This host-file ownership does not change the UID/GID values inside the archive.
Keep it protected until the restored deployment is verified, then remove it. If
replacement fails, keep the stack stopped and follow the cleanup steps below before
restoring the rollback archive. Each restore creates another rollback backup. When restoring
a rollback archive, the new `pre-restore.*` directory is placed beside the previous
one rather than nested inside it. A failed replacement keeps the verified rollback.

The archive layout is `backup/code-server-home/` (optionally with a leading `/`),
containing the entire `/home` tree. Numeric file ownership and symlinks are preserved.
Archives made before the `/home` mount change need their user files moved into
`cloud-user/` after restoration. Restore Homebrew onto the same CPU architecture.

Run `just test-backup` to exercise archive creation, in-place restoration, rollback
contents, hidden files, shared home directories, and ownership for local and S3 backups.

Restores reserve a Docker container named `code-server-restore-<volume hash>` for
their entire lifetime, so two restores against the same volume cannot overlap.
If cancellation interrupts reservation creation, cleanup checks an invocation-specific
label and removes only its own container, using its immutable ID. If Docker cannot
confirm the result, the helper names the unresolved reservation and asks you to
inspect it before removing it or retrying.

## Clean up after an interrupted restore

An interrupted restore can leave a `.restore-staging-<32 lowercase hex digits>`
directory at the root of the target Docker volume. The restore helper reports
the existing staging paths and stops before extracting another tree.

1. Keep the deployment stopped. Set `volume` to the exact Docker volume name and
   check which containers use it. If a restore reservation remains, inspect its
   mount and remove that specific container only after confirming the prior restore
   has stopped.

   ```sh
   volume=code-server_code-server-home
   docker volume inspect "$volume"
   docker ps -a --filter "volume=$volume"
   ```

2. Set `stage` to the directory name reported by the helper, without the leading
   slash. Inspect that directory through a read-only mount. Keep the verified
   rollback archive and save any staged files you need before removing the directory.

   ```sh
   stage=.restore-staging-0123456789abcdef0123456789abcdef
   mount="type=volume,src=$volume,dst=/restore"
   docker volume inspect "$volume" >/dev/null &&
     docker run --rm --network none --mount "$mount,readonly" \
       --entrypoint /bin/ls debian:13-slim -ld -- "/restore/$stage"
   ```

3. Remove the inspected staging directory by its exact name. Repeat for each
   reported directory after inspecting it.

   ```sh
   docker volume inspect "$volume" >/dev/null &&
     docker run --rm --network none --mount "$mount" \
       --entrypoint /bin/sh debian:13-slim -ec '
         suffix=${1#.restore-staging-}
         test "$suffix" != "$1" && test "${#suffix}" -eq 32 || exit 1
         case "$suffix" in *[!0-9a-f]*) exit 1 ;; esac
         path="/restore/$1"
         test -d "$path" && test ! -L "$path" || exit 1
         rm -rf -- "$path"
       ' sh "$stage"
   ```

4. If replacement began, run the printed recovery command with the verified rollback
   archive and the same volume. Otherwise, retry with the original input archive.
