[← Project overview](../README.md)

## Deploy

Complete [installation](installation.md) and [configuration](configuration.md#setup),
then start the deployment with `manage --pull` as described below.

### Launch options

Run `./manage` from a checkout, or `sudo /opt/code-server/manage` for
the default installation with `sudo`. For an installation without `sudo`, run
`/path/to/installation/manage` as the same user that installed it.
Keep `manage` in the same directory as `compose.yaml`.
The launcher always uses `.env` and Compose files beside itself, regardless of
your current working directory. It starts services in the background by default
and is included by the installer. Installed deployments include runtime Compose files, `manage`, `manage.py`,
`restore-home.sh`, `backup-key.py`, and `.env`. Management creates `.env.lock`,
and installer refreshes preserve previous files as `*.bak`. Build files and source
code stay in the checkout.

```sh
sudo /opt/code-server/manage --backup local --pull
./manage --foreground
/another/deployment/manage --project-name my-workspace
```

| Option | Purpose |
| --- | --- |
| `--backup none\|s3\|local` | Choose one backup mode; defaults to `none`. |
| `--foreground` | Attach to logs instead of running in the background. |
| `--pull` | Pull current images for the configured tags and recreate containers whose image changed. |
| `--project-name NAME` | Set the Compose project name. |
| `--help` | Show usage. |

Configure the selected backup mode in `.env` before starting. Use the same launch
options when applying updates so the same services and project are selected.

### Upgrade to the latest release

Run `sudo /opt/code-server/manage upgrade` to set `CODE_SERVER_IMAGE` in
`.env` to the latest stable GitHub release version, then run
`sudo /opt/code-server/manage --pull` with your usual launch options to apply it.
Omit `sudo` for installations owned by your user. The upgrade command uses the
GitHub owner/repository from the configured GHCR image, preserves other settings,
and does not pull images or restart services. Failed release lookups leave
`.env` unchanged. It replaces an existing image digest pin with the release tag.

### Shut down containers

Stop the deployment and remove its containers and networks while keeping all volumes:

```sh
./manage stop
```

Use `sudo` if installed with `sudo`, and pass the same `--project-name` if you set one.
This also removes the project's optional backup container. You can include the same
`--backup local` or `--backup s3` option used to start it. Start the deployment again
with `./manage` and your usual options.

Nested Docker stores images, stopped containers, and build cache in `docker-data`.
Monitor free disk space on the VM. Review `docker compose exec code-server docker system df`
and periodically run `docker compose exec code-server docker system prune` to
remove unused data after reviewing its prompt. Docker volumes require separate
retention decisions; home backups do not include nested Docker data.

### Backup and restore commands

Run a backup immediately using the configured local or S3 destination:

The examples below use `./manage`; add `sudo` if you installed with `sudo`.

```sh
./manage backup --backup local
./manage backup --backup s3
```

Use the same `--project-name` as the running deployment, if you set one. Start
the deployment with the matching `--backup` mode first so code-server has the
label that allows the backup service to stop it while archiving and restart it
afterward. The command waits for the backup to finish.

List local `.tar.gz.gpg` archives (and legacy `.tar.gz` archives) with their sizes and modification times:

```sh
./manage backups --backup local
```

This lists files in the configured `BACKUP_LOCAL_PATH`. To browse S3 or R2
archives, use your storage provider's console or CLI.

For recovery, follow the [restore guide](restore.md) to identify the home volume,
stop the deployment, and restore an archive with its original encryption key.
It covers validation, rollback, ownership, and restarting after recovery.
See [backups](backups.md) for destination settings, scheduling, and retention.

### Deploy a local build

For Compose-based builds, configure `.env` as above; `CODE_SERVER_IMAGE` is not required.

Without backups:

```sh
docker compose -f compose.yaml -f compose.build.yaml up -d --build
docker compose -f compose.yaml -f compose.build.yaml logs -f
docker compose -f compose.yaml -f compose.build.yaml down
```

For backups, add `-f compose.backup-common.yaml` and one destination overlay
(`-f compose.backup.yaml` for S3 or `-f compose.backup-local.yaml` for local storage)
to the same commands. Configure the destination and encryption key as described
in [backups](backups.md). For example:

```sh
docker compose -f compose.yaml -f compose.build.yaml -f compose.backup-common.yaml -f compose.backup-local.yaml up -d --build
```

Use the same file combination for subsequent commands. Before disabling backups,
run `down` with that combination to remove the backup service too. Volumes are kept.
