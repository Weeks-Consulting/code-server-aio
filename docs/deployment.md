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

Set the backup destination in `.env` before configuring. For local backups, first
prepare the archive directory as described in [local backup setup](backups.md#optional-local-backups).

```sh
./manage configure --backup local --pull --start
./manage configure --backup s3 --project-name my-workspace --start
```

Backup mode defaults to `none` on the first configure. Later configuration runs retain the saved backup
mode and project name unless you override them. A project name change requires the saved
project to have no running containers or Docker volumes on the active daemon.
Use `--backup none` to disable backups. Obsolete service containers are removed
during configuration; volumes remain.

Choose a distinct `--project-name` on first configuration for each deployment
that shares a Docker daemon. The `code-server-aio-dev-` prefix is reserved for
development runs. Compose labels each service container with its project name.
New or recreated deployment containers also carry the production run-mode label.
To find the containers for a project named `my-workspace`, use:

```sh
docker ps -a --filter label=io.github.weeks-consulting.code-server-aio.run-mode=production \
  --filter label=com.docker.compose.project=my-workspace
```

The resolved Compose configuration, including backup settings, project name,
image references, and memory limit, is saved in `.manage.compose.json` with mode
`600`. It contains secrets; protect it like `.env`. Do not edit it by hand.
Configuration writes use `.manage.compose.json.lock`. Compose rendering also
holds `.env.lock`, shared with install and upgrade, while reading deployment files.
Temporary snapshots are removed on failure or interruption (see
[interruptions](#interruptions)). The saved file is replaced only after container creation succeeds.
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
| `backup` | Exactly one of `--run`, `--list`, or `--restore`. `--run` requires a running backup service. `--list` lists configured local or S3 archives. `--restore --archive FILE_OR_URI [--volume NAME]` accepts local files and S3 URIs and defaults to the saved home volume. |
| `install` | `[--directory PATH] [--repository OWNER/REPO] [--version TAG] [--dry-run]` |

Before creating containers, `configure` pulls any configured image that is missing
locally, so a first configure or a changed tag may download images even without `--pull`.
`configure --pull` refreshes all configured images. Pulls show progress and finish before
any reservation or backup lock is taken. `--start` starts the containers after configuration. For attached output, use `manage logs --follow` after starting.
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
project name to create the saved configuration. For local backups, verify the
directory and its marker first. For example,
`./manage configure --backup local --project-name my-workspace --start`.

When `configure --start`, `start`, or `upgrade` starts a digest-pinned
Cloudflare or backup sidecar, it adds a local image tag such as
`cloudflare/cloudflared:2026-11-15-aaaaaaaaaaaa`. The date comes from the
image's creation metadata and the suffix is its short image ID. Docker uses
the digest from the deployment configuration, and the tag makes the image
easier to identify in `docker system df -v`.

### Interruptions

`manage` treats Ctrl-C, a hangup (for example, a dropped SSH session), and `SIGTERM`
the same way: it stops, then removes its temporary files. These are configuration
snapshots and install staging directories, and both contain secrets. A signal that you
deliberately ignore stays ignored, so `nohup ./manage configure --pull --start` keeps
running after you log out. `SIGKILL` cannot be handled. After one, delete any leftover `.manage.*.json` file in
the deployment directory, and inspect any `.install-*` directory before removing it: an
interrupted rollback keeps recovery files there.

If an interrupted `start` or `configure` reports a retained or unverified reservation,
inspect the reported container with `docker inspect CONTAINER` on the same Docker
daemon. Verify that the earlier Docker operation has finished and that the reservation
belongs to that invocation before removing it or retrying. The reservation carries
a `code-server.lifecycle-owner` label.

### Upgrade to the latest release

Published images check GitHub for newer stable releases in the background.
When one is available, the first authenticated visit to the IDE each UTC day
can show an update notice with release notes and a button to continue. Clicking
Continue sets a first-party cookie that dismisses the release shown on that
browser for the rest of the UTC day. A splash for a previous day or a different
release cannot dismiss the current notice.
Checking for updates is optional for workspace access, so GitHub outages leave
the IDE accessible. Weekly rebuilds of the same release tag do not trigger a notice.

```sh
./manage upgrade
```

`upgrade` installs the latest stable GitHub release, pulls the deployment images,
then recreates and starts the deployment.
To select a specific release, including a prerelease you want to test, use:

```sh
./manage upgrade --version v4.144.0-1
```

Both modes use the repository from your configured GHCR image, verify the release bundle
against `SHA256SUMS`, refresh release files (saving `.bak` copies), append new
`.env` settings, and replace the image setting, including an existing digest pin.
The saved backup mode and project name are retained. After the start succeeds,
the command attempts to remove the previous workspace image by ID. Images that are
still in use or have other tags are kept. No GitHub account is needed.
If the files install but the deployment fails to start, retry the upgrade or run
`./manage configure --pull --start`. Older managers that only install the release
files still need that configure step on their first upgrade.
In a source checkout, `upgrade` refuses to overwrite tracked files; check out the
release tag with git and set `CODE_SERVER_IMAGE` instead.

### Backup and restore commands

Prepare the archive directory before using the local backup commands below. See
[local backup setup](backups.md#optional-local-backups).

```sh
./manage configure --backup local --start
./manage backup --run
./manage backup --list
```

For S3 or R2 use `configure --backup s3 --start`. Manual backups execute inside
the running backup service and share its exclusive lock with scheduled runs.
They fail if it is stopped. After changing backup settings or the encryption key
in `.env`, run `configure --start` before taking a backup.

`backup --list` shows archives with sizes and modification times. For S3 or R2 it
prints an `s3://` URI for each archive that you can pass to `backup --restore --archive`.
Install the AWS CLI to use these commands for S3 or R2. They use the saved deployment settings.
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
