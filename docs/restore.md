# Restoring a backup

For local backups, select a `.tar.gz.gpg` archive (or a legacy `.tar.gz` archive) from `BACKUP_LOCAL_PATH`.
For S3 backups, download it using your storage provider's console or CLI.
Use the configured `AWS_S3_PATH`; older backups keep their original locations.

For encrypted archives, install `gpg` and `python3` on the Docker host (`sudo apt-get install gpg python3`
on Debian, or `brew install gnupg python` on macOS). Put the **original** `GPG_PASSPHRASE` in the deployment's `.env`;
`manage restore` reads it without executing the file. An exported `GPG_PASSPHRASE`
can override it to restore an archive encrypted with an older key. Do not generate a
new key for recovery: it cannot decrypt old backups. On a replacement host, recover
the original key from your password manager first.

The helper decrypts into a private temporary directory beside the archive and
removes the plaintext on exit. On macOS, place the archive in a folder shared with
Docker; the helper also writes its rollback archive there. A missing or incorrect key stops restoration before any volume changes.
Legacy plaintext `.tar.gz` archives need no key or host GPG installation.

Restore replaces the contents of the original home volume. The helper first
extracts the requested archive into temporary container storage, then creates
and verifies a compressed backup of the original volume before removing any data.
A helper container explicitly runs as root (`--user 0:0`) to extract numeric
UID/GID values and preserve file ownership, permissions, and symlinks in the volume.
The host command itself can run as any user with Docker access.
Allow space for the extracted archive and the rollback backup.

1. Identify the home volume before stopping the deployment:

   ```sh
   docker compose ps -q code-server | xargs docker inspect --format '{{range .Mounts}}{{if eq .Destination "/home"}}{{.Name}}{{end}}{{end}}'
   ```

   Use your usual Compose project name and file options for this command and `down`.
2. Stop the stack with `sh manage stop` (and your usual `--project-name`, if set),
   or `docker compose down` using those same Compose options. Do not use
   `down -v`, which deletes volumes. Stop any other containers using the home volume.
3. Run the launcher with the actual volume name:

   ```sh
   sh manage restore --archive /absolute/path/to/backup.tar.gz.gpg --volume code-server_code-server-home
   ```

   For the default installation with `sudo`, use
   `sudo sh /opt/code-server/manage restore ...`. Otherwise, run as the user
   that installed the deployment. The standalone helper accepts the same archive
   and volume as positional arguments: `sh build/tools/restore-home.sh ARCHIVE VOLUME`
   (installed deployments have `restore-home.sh` beside `manage`). For encrypted
   archives, the standalone helper requires an exported `GPG_PASSPHRASE`; it does
   not read `.env`. When using `sudo`, the launcher reads `.env` as root; to use an
   exported older key on Debian, pass it with `sudo --preserve-env=GPG_PASSPHRASE sh ...`.
4. Restart with your usual `manage` options and verify files, extensions,
   Homebrew, and sign-in. The volume name stays unchanged; no Compose override is needed.

The target volume must already exist and have no running containers attached.
Relative archive paths resolve from your current working directory. Keep the stack
stopped throughout restoration; the helper does not restart it automatically.

The helper prints the rollback archive path after verifying it and before replacing
volume contents. Failed extraction leaves no empty rollback directory. It saves
`original-home.tar.gz` in a new private `pre-restore.*` directory beside the input
archive. This emergency rollback is **unencrypted**, with mode `600` inside a
mode `700` directory. Both belong to the effective user running `manage restore`
(or the standalone helper), so that user can read and copy the rollback archive.
If the command runs as root, root owns these files; sudo environment variables are
ignored. This host-file ownership does not change the UID/GID values inside the archive.
Keep it protected until the restored deployment is verified, then remove it. If
replacement fails, keep the stack stopped and restore that rollback archive using
the same command and volume. Each restore creates another rollback backup. When restoring
a rollback archive, the new `pre-restore.*` directory is placed beside the previous
one rather than nested inside it. A failed replacement keeps the verified rollback.

The archive layout is `backup/code-server-home/` (optionally with a leading `/`),
containing the entire `/home` tree. Numeric file ownership and symlinks are preserved.
Archives made before the `/home` mount change need their user files moved into
`cloud-user/` after restoration. Restore Homebrew onto the same CPU architecture.

Run `just test-backup` to exercise archive creation, in-place restoration, rollback
contents, hidden files, shared home directories, and ownership without S3.
