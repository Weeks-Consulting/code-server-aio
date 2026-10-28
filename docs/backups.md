[← Project overview](../README.md)

## Backup encryption

Both Offen backup overlays require GPG encryption and write `.tar.gz.gpg` archives.
The installer generates a random 256-bit `BACKUP_GPG_PASSPHRASE` in `.env` if it is missing
or empty, including when refreshing an existing installation. When using a checkout,
`manage --backup local` or `manage --backup s3` generates it before starting services;
manual backup commands also initialize it if necessary. Existing nonempty keys are
preserved. Generated keys are never printed, and `.env` is saved with mode `600`.
The initializer uses Python 3 and a persistent `.env.lock` sidecar to serialize
concurrent launches without replacing an existing key.
You can set your own passphrase in `.env` before installation or startup.

**Save `BACKUP_GPG_PASSPHRASE` separately in a password manager or another secure location.**
The home-volume backup does not include the host deployment's `.env`. Losing the key
makes encrypted archives unrecoverable. Keep old keys if you change the passphrase;
older archives still need the key used to create them. Restart the backup service
with your usual launch options after changing the key. Existing plaintext archives
remain unchanged.

For direct Compose use, first set a passphrase in `.env` or generate one with
`python3 build/tools/backup-key.py .env` from a checkout (`python3 backup-key.py .env`
in an installed deployment). Compose refuses to start a backup service without a key.
Use the same key when recovering on a replacement host. Restore needs `gpg` and `python3` installed
on the Docker host, both checked and installed by the deployment installer; backup creation
needs no host GPG installation. See Offen's [encryption documentation](https://offen.github.io/docker-volume-backup/how-tos/encrypt-backups.html).

## Optional local backups

With `./manage --backup local`, new archives belong to the effective user running
`manage` (`id -u` and `id -g`). If that user is root, root owns the files.
The launcher does not inspect sudo environment variables.
Scheduled backups retain that ownership configuration until the backup service
is recreated. Before copying archives, the dedicated backup directory is assigned
to that user and made private (mode `700`). Completed archives have mode `600`;
file ownership inside
the archives is preserved. Existing archives are unchanged.

To apply this to an existing deployment, rerun `manage --backup local` with your
usual options (and `sudo` if installed with `sudo`). This recreates the backup
service with the updated configuration.

For direct Compose commands, export `BACKUP_LOCAL_UID` and `BACKUP_LOCAL_GID`
with the desired host IDs; they default to `0:0` when the launcher is not used.
For example, `export BACKUP_LOCAL_UID=$(id -u) BACKUP_LOCAL_GID=$(id -g)` before
the commands below. With `sudo docker compose`, pass them explicitly:
`sudo env BACKUP_LOCAL_UID="$BACKUP_LOCAL_UID" BACKUP_LOCAL_GID="$BACKUP_LOCAL_GID" docker compose ...`.

Set `BACKUP_LOCAL_PATH` in `.env` (default `./backups`), then include the local overlay:

```sh
docker compose -f compose.yaml -f compose.backup-common.yaml -f compose.backup-local.yaml up -d
docker compose -f compose.yaml -f compose.backup-common.yaml -f compose.backup-local.yaml logs -f backup
# Trigger an immediate backup:
docker compose -f compose.yaml -f compose.backup-common.yaml -f compose.backup-local.yaml exec backup backup
```

Archives are stored in the host folder with names such as
`code-server-home-2026-10-21T03-00-00.tar.gz.gpg`. The schedule defaults to 03:00 in
`BACKUP_TIMEZONE`, using the same stop/archive/restart behavior as S3 backups.
S3 settings in `.env` are ignored by this overlay. Archives are not automatically
deleted; manage local retention yourself. See [Restoring a backup](restore.md)
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
configure bucket lifecycle rules for retention.

Backup stop labels and archive names are scoped to the Compose project. The
default S3 prefix is `<project>/home`. If multiple hosts share a bucket, set a
unique `BACKUP_ID` on each host; it controls archive names and the default prefix.
An explicit `BACKUP_AWS_S3_PATH` still overrides the prefix.

`BACKUP_AWS_ENDPOINT` is a hostname without `https://`. `BACKUP_AWS_S3_PATH` sets the object
prefix; `BACKUP_AWS_S3_BUCKET_LOOKUP` controls addressing (`auto`, `path`, or `dns`).
For R2, use your account's R2 endpoint and S3 credentials; `auto` normally works,
or use `path` explicitly.

Use all three `-f` options for subsequent commands, including `down`. To disable backups:

```sh
docker compose -f compose.yaml -f compose.backup-common.yaml -f compose.backup.yaml rm --stop --force backup
```

Then return to base Compose commands. See the [backup reference](https://offen.github.io/docker-volume-backup/reference/)
for more options.

For recovery instructions, see [Restoring a backup](restore.md). The restore
helper backs up the original volume before replacing its contents in place.

