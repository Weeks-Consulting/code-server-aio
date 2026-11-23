[← Project overview](../README.md)

## Backup encryption

Both Offen backup overlays require GPG encryption and write `.tar.gz.gpg` archives.
The installer generates a random 256-bit `BACKUP_GPG_PASSPHRASE` in `.env` if it is missing
or empty, including when refreshing an existing installation. When using a checkout,
`manage configure --backup local --start` or `manage configure --backup s3 --start` generates it before starting services.
Manual backups use the running service's key and do not generate a replacement. Existing nonempty keys are
preserved. Generated keys are never printed, and `.env` is saved with mode `600`.
The initializer uses Python 3 and a persistent `.env.lock` sidecar to serialize
concurrent configuration runs without replacing an existing key.
Let `manage` generate the key. If you supply one yourself, use exactly 64 lowercase
hexadecimal characters (`0–9` and `a–f`). Install and configuration reject invalid
existing keys without replacing them.

`manage backup --run` runs inside the existing backup service, using
its configuration and exclusive lock. Scheduled and manual backups wait for
each other. Start the matching backup mode first; a stopped backup service causes
the command to fail. Recreate the service with `manage configure --start`
after changing its settings or encryption key.

`manage start`, `manage stop`, and `manage configure` also acquire the running
backup service's lock before changing containers. If a manual or scheduled
backup holds the lock, wait for it to finish and retry the lifecycle command.

**Save `BACKUP_GPG_PASSPHRASE` separately in a password manager or another secure location.**
The home-volume backup does not include the host deployment's `.env`. Losing the key
makes encrypted archives unrecoverable. Keep old keys if you change the passphrase;
older archives still need the key used to create them. Reconfigure the backup service
with `manage configure --start` after changing the key. Existing plaintext archives
remain unchanged.

For direct Compose use, first set a passphrase in `.env` or generate one with
`python3 build/tools/backup-key.py .env` from a checkout (`python3 backup-key.py .env`
in an installed deployment). The backup service requires a key in the same format.
Use the same key when recovering on a replacement host. Restore needs `gpg` and `python3` installed
on the Docker host. The installer never installs host packages and does not check for `gpg`;
install it before you need to recover, especially on a replacement host. Backup creation
needs no host GPG installation. See Offen's [encryption documentation](https://offen.github.io/docker-volume-backup/how-tos/encrypt-backups.html).

## Optional local backups

With `./manage configure --backup local --start`, new archives belong to the effective user running
`manage` (`id -u` and `id -g`). If that user is root, root owns the files.
The launcher does not inspect sudo environment variables.
Scheduled backups retain that ownership configuration until the backup service
is recreated. Completed archives have mode `600` and preserve file ownership inside
the archive.

Create a dedicated directory before configuring local backups. For the default
`BACKUP_LOCAL_PATH=./backups`, run this from the deployment directory as the same
user who runs `manage`:

```sh
mkdir -m 700 ./backups && touch ./backups/.code-server-aio-backups
```

If you already have a dedicated backup directory, confirm its owner and group
match the configured numeric IDs and its mode is `700`, then create the marker
file there. With a remote Docker daemon, prepare the path on the daemon host.
The backup service checks the directory at startup and again before copying or
pruning archives. Apply these settings with `manage configure --backup local --start`.

For direct Compose commands, export `BACKUP_LOCAL_UID` and `BACKUP_LOCAL_GID`
with the desired host IDs; they default to `0:0` when the launcher is not used.
For example, `export BACKUP_LOCAL_UID=$(id -u) BACKUP_LOCAL_GID=$(id -g)` before
the commands below. With `sudo docker compose`, pass them explicitly:
`sudo env BACKUP_LOCAL_UID="$BACKUP_LOCAL_UID" BACKUP_LOCAL_GID="$BACKUP_LOCAL_GID" docker compose ...`.

Set `BACKUP_LOCAL_PATH` in `.env` (default `./backups`), prepare the directory with
the selected ownership, then include the local overlay:

```sh
docker compose -f compose.yaml -f compose.backup-common.yaml -f compose.backup-local.yaml up -d
docker compose -f compose.yaml -f compose.backup-common.yaml -f compose.backup-local.yaml logs -f backup
# Trigger an immediate backup:
docker compose -f compose.yaml -f compose.backup-common.yaml -f compose.backup-local.yaml exec backup backup
```

Archives are stored in the host folder with names such as
`home.code-server.2026-10-21T03-00-00-123456-0500.tar.gz.gpg`. The timestamp includes
microseconds and the numeric UTC offset. The schedule defaults to 03:00 in
`BACKUP_TIMEZONE`, using the same stop/archive/restart behavior as S3 backups.
S3 settings in `.env` are ignored by this overlay. Matching archives older than
`BACKUP_RETENTION_DAYS` (default seven days) are automatically pruned. Set `-1`
to disable pruning. Pruning is restricted to the `home.<backup-id>.`
filename prefix; use a dedicated directory and a unique backup ID. See [Restoring a backup](restore.md)
to restore directly from a local archive.

Use the same three-file combination for subsequent commands, including `down`. To
switch between local and S3 backups, run `up -d` with the chosen overlay; Compose
recreates the backup service for that destination. Include the common overlay and only one destination overlay.

## Optional S3 backups

Set the S3 variables in `.env`, then include the S3 backup overlay:

```sh
docker compose -f compose.yaml -f compose.backup-common.yaml -f compose.backup.yaml up -d
docker compose -f compose.yaml -f compose.backup-common.yaml -f compose.backup.yaml logs -f backup
# Trigger an immediate backup:
docker compose -f compose.yaml -f compose.backup-common.yaml -f compose.backup.yaml exec backup backup
```

Backups run daily at **03:00 America/Chicago**, configurable through
`BACKUP_TIMEZONE` and `BACKUP_CRON_EXPRESSION`. The backup service uses the host
Docker socket to stop code-server while archiving the entire `/home` volume, then restart it.
Nested Docker data is excluded. Timestamped archives upload to an existing bucket;
matching archives older than seven days are pruned by default. Set
`BACKUP_RETENTION_DAYS` to change this policy (`-1` disables pruning), and ensure
the S3 credentials allow listing and deleting matching objects. Bucket lifecycle
rules can impose additional retention.

Backup stop labels use the Compose project name. Archive names and pruning use
`BACKUP_ID`, which defaults to the Compose project name. The default S3 object
prefix is `<backup-id>/home`. Set a unique `BACKUP_ID` on each host that shares a
bucket. Set `BACKUP_AWS_S3_PATH` to override the S3 object prefix.

`BACKUP_AWS_ENDPOINT` is a hostname without `https://`. `BACKUP_AWS_S3_PATH` sets the object
prefix; `BACKUP_AWS_S3_BUCKET_LOOKUP` controls addressing (`auto`, `path`, or `dns`).
For R2, use your account's R2 endpoint and S3 credentials; `auto` normally works,
or use `path` explicitly.

With the AWS CLI installed, `./manage backup --list` lists the configured prefix and
prints an `s3://` URI for each archive. Use one of those URIs to
[restore directly from S3 or R2](restore.md).

Use all three `-f` options for subsequent commands, including `down`. To disable backups:

```sh
docker compose -f compose.yaml -f compose.backup-common.yaml -f compose.backup.yaml rm --stop --force backup
```

Then return to base Compose commands. See the [backup reference](https://offen.github.io/docker-volume-backup/reference/)
for more options.

For recovery instructions, see [Restoring a backup](restore.md). The restore
helper backs up the original volume before replacing its contents in place.


## Backup monitoring

Set `BACKUP_NOTIFICATION_URLS` to a comma-separated list of Shoutrrr notification
URLs. `BACKUP_NOTIFICATION_LEVEL=error` reports failures; `info` reports every
completed run. Notification credentials stay in `.env`. See the upstream
[notification guide](https://offen.github.io/docker-volume-backup/how-tos/set-up-notifications.html).
Use success notifications or storage monitoring to detect a stopped scheduler as
well as failed runs, and periodically run `just test-backup` and restore a real archive
to a disposable volume to check recovery.
