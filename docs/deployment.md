[← Project overview](../README.md)

## Deploy

Complete [installation](installation.md) and [configuration](configuration.md#setup),
then create and start the deployment:

```sh
~/code-server/manage configure --pull --start
```

### Configure containers

Run `./manage` from a checkout, or `~/code-server/manage` for the default installation.
Use the same user that installed it, with `sudo` if installed with `sudo`.
Commands use the deployment directory beside the launcher regardless of your current directory.

`configure` reads `.env` and the selected Compose files, sizes workspace memory at
80% of the Docker host's total, and creates or recreates containers. It may stop
containers whose configuration changes. By default it leaves newly created
containers stopped; add `--start` to start the deployment afterward.

```sh
./manage configure --backup local --pull --start
./manage configure --backup s3 --project-name my-workspace --start
```

Configure your selected backup destination in `.env` first. Backup mode defaults
to `none` on the first configure. Later configuration runs retain the saved backup
mode and project name unless you override them. Use `--backup none` to disable
backups. Obsolete service containers are removed during configuration; volumes remain.

The resolved Compose configuration, including backup settings, project name,
image references, and memory limit, is saved in `.manage.compose.json` with mode
`600`. It contains secrets; protect it like `.env`. Do not edit it by hand.
Configuration writes use `.manage.compose.json.lock`, and temporary snapshots are
removed on failure. The saved file is replaced only after container creation succeeds.
Docker may already have changed some containers if creation fails; correct the
error and rerun `configure` to finish applying the configuration.

Installed deployments include runtime Compose files, `manage`, `manage.py`,
`restore-home.sh`, `backup-key.py`, and `.env`. Installer refreshes preserve old
release files as `*.bak` and keep the saved configuration. Build files stay in the checkout.

### Command option reference

Commands and options may appear in any order. A command is required.
Every command accepts `-h` or `--help`. Brackets mark optional arguments.

| Command | All applicable options |
| --- | --- |
| `configure` | `[--backup none\|s3\|local] [--project-name NAME] [--pull] [--start]` |
| `start` | `[--pull]`; start saved deployment, optionally refreshing its images. |
| `stop` | No command-specific options; stop existing containers. |
| `logs` | `[-f\|--follow]` |
| `upgrade` | `[--version TAG]`; install selected release files and image setting (default: latest stable). |
| `backup` | Exactly one of `--run`, `--list`, or `--restore`. `--run` requires configured, running local or S3 backups; `--list` requires configured local backups; `--restore` requires `--archive FILE --volume NAME`. |
| `install` | `[--directory PATH] [--repository OWNER/REPO] [--version TAG] [--dry-run]` |

`configure --pull` pulls configured images before creating containers. `--start` starts them
after configuration. For attached output, use `manage logs --follow` after starting.
See [installation](installation.md) for install option defaults and
[restore](restore.md) for archive and volume requirements.

### Start, stop, and logs

After configuring once, use:

```sh
./manage stop
./manage start
./manage logs --follow
```

These commands use the saved project and backup configuration. `stop` keeps
containers, networks, and volumes; `start` resumes those containers without
pulling images, recalculating memory, or applying changed settings. To refresh
images for the saved tags and start the deployment, use:

```sh
./manage start --pull
```

This pulls all configured service images and recreates containers as needed using
the saved configuration, including the project name and backup mode. A digest pin
stays on that digest. Pull or creation failures stop the command before startup.
Changes to
`.env`, Compose files, and image tags take effect when you run `configure` again.
If containers were deleted, recreate them with `configure --start`.

**Existing deployments:** run `configure` once with your existing backup mode and
project name to create the saved configuration. For example,
`./manage configure --backup local --project-name my-workspace --start`.

### Upgrade to the latest release

```sh
./manage upgrade
./manage configure --pull --start
```

`upgrade` installs the latest stable GitHub release without changing containers.
To select a specific release, including a prerelease you want to test, use:

```sh
./manage upgrade --version v4.144.0-1
./manage configure --pull --start
```

Both modes use the repository from your configured GHCR image, verify the release bundle
against `SHA256SUMS`, refresh release files (saving `.bak` copies), append new
`.env` settings, and replace the image setting, including an existing digest pin.
Review new settings, then run `configure --pull --start`; the saved backup mode and
project name are retained. No GitHub account is needed.
In a source checkout, `upgrade` refuses to overwrite tracked files; check out the
release tag with git and set `CODE_SERVER_IMAGE` instead.

### Backup and restore commands

```sh
./manage configure --backup local --start
./manage backup --run
./manage backup --list
```

For S3 or R2 use `configure --backup s3 --start`. Manual backups execute inside
the running backup service and share its exclusive lock with scheduled runs.
They fail if it is stopped. After changing backup settings or the encryption key
in `.env`, run `configure --start` before taking a backup.

`backup --list` lists local archives with sizes and modification times. Browse S3 or R2
archives with your storage provider's console or CLI.
See [backups](backups.md) for scheduling and retention, and [restore](restore.md)
for volume selection, encryption keys, validation, and rollback.

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
