# All-in-One Development Environment with Cloudflare Access

Deploy a complete, browser-based development environment with code-server and Docker-in-Docker using Docker Compose. Run it on a Docker host and access it remotely through Cloudflare Tunnel, with authentication enforced through Cloudflare Access policies and local token validation.

## AI disclosure

AI tools assisted with the development, documentation, and review of this project.
The entire project has also been reviewed and tested by a real person.

## Install

On a Linux machine with Docker Engine, the Docker Compose plugin, Bash, and curl:

```sh
curl -fsSL https://raw.githubusercontent.com/Weeks-Consulting/code-server-aio/main/build/tools/install.sh -o /tmp/install-code-server.sh && sudo bash /tmp/install-code-server.sh
```

The installer creates `/opt/code-server`, downloads `compose.yaml` and the optional
S3 and local backup overlays from the latest stable GitHub release, and creates a `.env`
with that release's GHCR image selected. Rerunning the installer refreshes the
Compose files, saving their previous contents as `.bak` files. Existing `.env`
values and comments are preserved; missing settings are appended with notes from
the new release. `.env` is readable only by its owner. Services are not started
or restarted automatically.

To update, download and rerun the installer using the command above. Review any
new `.env` settings before restarting. Your existing `CODE_SERVER_IMAGE` is also
preserved: change it explicitly in `.env` to use a newer image release, then run
`sudo docker compose up -d --pull always` with your usual Compose file options.

To install a specific release or release candidate, add `--version v4.141.0` or
`--version v4.141.0-rc.1`. Use `--directory /another/path` to change the destination.
The release's image must have finished publishing before you start the deployment.

```sh
cd /opt/code-server
sudoedit .env
```

Complete the configuration below, then run `sudo docker compose up -d --pull always`
from `/opt/code-server`. To enable backups, configure the S3 settings and include
`-f compose.yaml -f compose.backup.yaml`, or use `-f compose.yaml -f compose.backup-local.yaml`
for local-folder backups as described below.

## Setup

Anyone granted Cloudflare Access to this privileged development environment
effectively has root access to the Docker host. Restrict the Access policy to
specific trusted users.

1. Edit the installer's `.env` (or copy `.env.example` when using a checkout) and set:
   - `CODE_SERVER_IMAGE`: the published GHCR image (selected by the installer; defaults to the project image in a checkout).
   - `CLOUDFLARE_TUNNEL_TOKEN`: your remotely managed tunnel token.
   - `CF_ACCESS_TEAM_DOMAIN`: your `team.cloudflareaccess.com` hostname, without `https://`.
   - `CF_ACCESS_AUD`: the 64-character Application Audience tag from your Access application.
2. Configure the tunnel origin as `http://code-server:8080` and protect the hostname
   with an Access application and an Allow or Service Auth policy.
3. Start the services:

   ```sh
   docker compose up -d --pull always
   ```

Find the AUD under **Zero Trust → Access controls → Applications → Configure →
Additional settings**. Open your public hostname and sign in through Access.

## Environment configuration

Edit `.env` beside the Compose files. Values such as `replace-with-...` and
`your-team` in `.env.example` are placeholders to replace, not working defaults.
Compose uses the defaults below when an optional value is unset or empty.
Variables exported in your shell take precedence over `.env`.

After changing runtime settings, rerun `docker compose up -d` with your usual
Compose file options to recreate affected containers. `docker compose restart`
does not apply environment changes. Changing a build version also requires
`--build`. Backups are enabled by including `compose.backup.yaml` (S3) or
`compose.backup-local.yaml` (local folder); use one backup overlay at a time.
Setting backup variables alone does not enable them.

### Application and local builds

| Property | Required / default | Description |
| --- | --- | --- |
| `CODE_SERVER_IMAGE` | Optional; `ghcr.io/weeks-consulting/code-server-aio:latest`. | Full registry image reference to pull, such as `ghcr.io/weeks-consulting/code-server-aio:4.141.0` or `:4.141.0-rc.1`. The installer selects the requested release on first installation and preserves your choice on updates. `latest` follows stable releases. Ignored when `compose.build.yaml` selects the local image. |
| `CODE_SERVER_VERSION` | Optional; `build/code-server.version`. | Overrides the upstream code-server version for local builds, such as `4.141.0`, without a leading `v`. Used only by `compose.build.yaml`; it does not change a prebuilt image. The package must have a checksum in `build/code-server.sha256`, so normally update `build/code-server.version` instead. |
| `CLOUDFLARE_TUNNEL_TOKEN` | Required; no default. | Tunnel token for your remotely managed Cloudflare Tunnel, used by the cloudflared sidecar. Configure the tunnel's origin service as `http://code-server:8080`. This is the tunnel token, not a Cloudflare API token or an Access service token. |
| `CF_ACCESS_TEAM_DOMAIN` | Required for access; no default. | Cloudflare Access team hostname, such as `my-team.cloudflareaccess.com`. Supply only the hostname, without `https://`, a path, or a trailing slash. HAProxy's helper downloads signing keys from this team and checks that JWTs have its issuer. Missing or invalid configuration keeps the origin blocked with HTTP 503. |
| `CF_ACCESS_AUD` | Required for access; no default. | The Access application's 64-character hexadecimal Application Audience (AUD) tag. Use the application protecting this service, not the tunnel ID or account ID. Tokens with a different audience receive HTTP 403; missing or malformed configuration results in HTTP 503. Find it under **Zero Trust → Access controls → Applications → Configure → Additional settings**. |
| `REPAIR_HOME_OWNERSHIP` | Optional; `false`. | Accepts exactly `true` or `false`. `true` recursively repairs ownership of `/home/cloud-user` to `cloud-user` (UID/GID 10000) at startup. `false` repairs only that directory's owner, preserving file owners required by nested containers. Other directories under `/home`, including Homebrew's directory, are outside this repair. Invalid values stop startup. |
| `HOMEBREW_INSTALL_TIMEOUT` | Optional; `600`. | Positive integer in seconds limiting the first or retried Homebrew installation. After this timeout, the installer receives TERM, then KILL after another 30 seconds if needed. Failure stops container startup and keeps a marker for retry on the next start. An existing complete Homebrew installation is reused, so this timeout does not apply to normal subsequent starts. Increase it for slow downloads; if necessary, also increase Compose's health-check `start_period` beyond its current 12 minutes. |

### Optional backups

The backup container controls the host through the Docker API. Mounting its
Docker socket read-only does not restrict API operations.

These properties apply when either backup overlay is included. S3 settings apply
only to `compose.backup.yaml`; local backups need no S3 credentials. Backups stop
the code-server container while archiving all of `/home`, then restart it. They
exclude nested Docker's `/var/lib/docker`. For S3, create the destination bucket
first and configure retention through its lifecycle rules. Local archives are
kept until you remove them; automatic pruning is not configured.

| Property | Required / default | Description |
| --- | --- | --- |
| `BACKUP_TIMEZONE` | Optional; `America/Chicago`. | Timezone used by the backup schedule. Use an IANA timezone such as `America/Chicago` or `Etc/UTC`. The schedule follows that timezone, including its daylight-saving changes. |
| `BACKUP_CRON_EXPRESSION` | Optional; `0 3 * * *`. | Five-field cron schedule: minute, hour, day of month, month, day of week. The default runs daily at 03:00 in `BACKUP_TIMEZONE`. Quote the value in `.env`, for example `BACKUP_CRON_EXPRESSION="0 3 * * *"`. |
| `BACKUP_LOCAL_PATH` | Optional; `./backups`. Local overlay only. | Host folder receiving `.tar.gz` archives. Relative paths resolve beside the Compose files, so the installer's default is `/opt/code-server/backups`. Use an absolute path such as `/mnt/backups/code-server` to select another location. The folder is mounted at `/archive` in the backup container. S3 settings are not passed to the local backup container. |
| `AWS_S3_BUCKET_NAME` | Required for S3 backups; no default. | Name of an existing S3 or S3-compatible bucket. Supply the bucket name alone, without `s3://`, an endpoint, or an object prefix. |
| `BACKUP_ID` | Optional; Compose project name. | Identifies this deployment in archive names and in the default S3 prefix. Set a unique value, such as `devbox-home`, when multiple hosts share a bucket. Archive names are `<BACKUP_ID>-home-<timestamp>.tar.gz`; an empty value uses the Compose project name. It does not change the container stop label, which remains scoped to the Compose project. |
| `AWS_S3_PATH` | Optional; `<BACKUP_ID or Compose project name>/home`. | Object-key prefix inside the bucket, such as `development/devbox/home`. An explicit value overrides the prefix derived from `BACKUP_ID`; an empty value selects the default. This controls where archives are stored, not the bucket addressing style. |
| `AWS_ENDPOINT` | Optional; `s3.amazonaws.com`. | S3 service hostname without `https://` or a bucket path. For Cloudflare R2, use `<account-id>.r2.cloudflarestorage.com`. Use the endpoint supplied by your storage provider. |
| `AWS_S3_BUCKET_LOOKUP` | Optional; `auto`. | Bucket addressing style: `auto` selects based on the endpoint; `path` addresses the bucket as `endpoint/bucket`; `dns` uses `bucket.endpoint`. R2 normally works with `auto`; use `path` when explicitly required by your endpoint. Independent of `AWS_S3_PATH`. |
| `AWS_ACCESS_KEY_ID` | Required for S3 backups; no default. | S3 access key ID with permission to upload backup objects to the selected bucket and prefix. For R2, use an R2 S3 access key ID, not a general Cloudflare API token. Pair it with `AWS_SECRET_ACCESS_KEY`. |
| `AWS_SECRET_ACCESS_KEY` | Required for S3 backups; no default. | Secret associated with `AWS_ACCESS_KEY_ID`. Use the S3-compatible credentials issued by your storage provider. |

Compose also accepts `COMPOSE_PROJECT_NAME`, although it is not listed in
`.env.example`. By default, the project name comes from the deployment directory
(`code-server` for `/opt/code-server`); `docker compose -p NAME` overrides it.
This name scopes Docker volumes, backup stop labels, and the default backup ID.
Changing it selects a different set of named volumes, so keep it consistent for
an existing deployment.

## Access protection

Traffic flows through cloudflared → HAProxy (`:8080`) → code-server (`127.0.0.1:8081`).
HAProxy validates Access JWT signatures, issuer, audience, and timestamps.

- **403:** Missing or invalid token, including an expired session or incorrect AUD.
- **503:** Invalid/missing Access settings, initial key download pending, or cached keys older than 24 hours.

Signing keys refresh hourly, with retries every minute on failure. Startup blocks
access until keys are fetched. The health check verifies supervised services,
nested Docker, code-server, and HAProxy's rejection of unauthenticated requests.
Cloudflared starts alongside the container so HAProxy can show its 503 setup page.
Customize `build/runtime/access-403.html` and `build/runtime/access-503.html` and rebuild.

## Operation and customization

```sh
docker compose logs -f
docker compose exec --user cloud-user code-server bash
docker compose exec code-server supervisorctl status
docker compose down
```

code-server runs as `cloud-user` (UID/GID 10000), with passwordless sudo and Docker
group access. Supervisor manages the services; the container runs privileged for
Docker-in-Docker. Startup repairs the home directory owner; recursive repair is opt-in.
If any supervised service exhausts its startup retries, Supervisor shuts down so
Docker's restart policy can restart the container. Missing Access configuration
reports unhealthy and keeps access blocked until corrected.

Save projects under `/home/cloud-user`; `/workspace` is not provided.

Named volumes persist all of `/home` (user files, settings, extensions, and shared
installations such as `/home/linuxbrew`) and
`/var/lib/docker`. `docker compose down` keeps them; `down -v` deletes them.

[Homebrew](https://docs.brew.sh/Homebrew-on-Linux) installs as `cloud-user` on first
startup under `/home/linuxbrew/.linuxbrew`, after mounts are available. This also
works with an empty `/home` bind mount. Initial setup needs Internet access and may
take several minutes; later starts reuse the installation without updating it.
`brew` is available in terminals and login shells. Run `brew update` explicitly
when needed. Installation is limited to 600 seconds by default; set
`HOMEBREW_INSTALL_TIMEOUT` to change it. Failure or timeout stops startup and
retains a marker so incomplete installations are retried.

Recursive ownership repair defaults to disabled. Set `REPAIR_HOME_OWNERSHIP=true` only for an intentional migration: it changes ownership of all home contents, including nested containers' database files, which can prevent those containers from starting. The home directory owner is still corrected.

If reusing the previous home-volume layout, move its existing contents into a
`cloud-user/` subdirectory before restarting with this mount. Ownership repair
remains scoped to `/home/cloud-user`, preserving ownership of other home directories.

Add apt packages in `build/Dockerfile`, one-time startup commands in `build/runtime/entrypoint.sh`, and
foreground services in `build/runtime/supervisord.conf`. Every supervised service is
critical: the health check requires it to be running, and repeated startup failure
restarts the container.

Files shipped in the application image live in `build/runtime/`, including startup
scripts, service configuration, and error pages. Build-only version and checksum
files stay beside `build/Dockerfile`. Host installation and restore helpers live
in `build/tools/`; tests stay in `build/tests/`. Compose files stay at the project root.

## Local development

Builds read the code-server version from `build/code-server.version`. `just build`
and `docker build -f build/Dockerfile .` need no `.env` or Cloudflare credentials.
For Compose-based builds, configure `.env` as above; `CODE_SERVER_IMAGE` is not required.

Without backups:

```sh
docker compose -f compose.yaml -f compose.build.yaml up -d --build
docker compose -f compose.yaml -f compose.build.yaml logs -f
docker compose -f compose.yaml -f compose.build.yaml down
```

With backups (also configure the S3 variables in `.env`):

```sh
docker compose -f compose.yaml -f compose.build.yaml -f compose.backup.yaml up -d --build
docker compose -f compose.yaml -f compose.build.yaml -f compose.backup.yaml logs -f
docker compose -f compose.yaml -f compose.build.yaml -f compose.backup.yaml exec backup backup
docker compose -f compose.yaml -f compose.build.yaml -f compose.backup.yaml down
```

For local-folder backups during development, set `BACKUP_LOCAL_PATH` and replace
`compose.backup.yaml` with `compose.backup-local.yaml` in those commands. S3
credentials are not needed.

The `exec backup backup` command triggers an immediate backup. Use the same file
combination for subsequent commands. Before switching from backups to no backups,
run the three-file `down` command to stop the backup service too. Volumes are kept.

## Image publishing

GitHub Actions builds and tests native amd64 and arm64 images on pull requests,
pushes to `main`/`master`, manual runs, weekly scheduled builds, and published
GitHub releases. Only published releases upload images; regular commits, manual
runs, and scheduled builds run tests only. Scheduled builds refresh the base image
and bypass cached package layers. CI covers full startup, Homebrew persistence,
service recovery, and a local backup/restore round trip.

To publish, update `build/code-server.version` (for example, `4.141.0`) and publish
a GitHub release with the matching tag, such as `v4.141.0`. CI, `just build`, and
Compose builds all read this file. CI rejects invalid versions
and mismatched release tags before building. Manually triggered runs on tags
also require a matching version. Pushing a tag alone does not publish.

After both architectures pass, the release publishes
`ghcr.io/<owner>/<repository>:4.141.0`, by assembling the tested image digests. This Docker-load publication path does not generate BuildKit provenance or SBOM attestations. Stable releases
also update `latest`; prereleases publish only the version tag. Image names are
derived from the repository and lowercased automatically. Publishing uses
`GITHUB_TOKEN`; no registry secret or naming variables are needed.

To test an image before its final release, keep `build/code-server.version` set to
the upstream version (for example, `4.141.0`) and publish a GitHub **pre-release**
tagged `v4.141.0-rc.1`. CI builds code-server `4.141.0` and publishes image tag
`4.141.0-rc.1`. Set `CODE_SERVER_IMAGE=ghcr.io/<owner>/<repository>:4.141.0-rc.1`
in `.env` to try it. Use `-rc.2`, `-rc.3`, etc. for further candidates, then publish
the final `v4.141.0` release when ready. RC tags never update `latest`, even if
the GitHub release is accidentally marked stable. The base version of every
RC tag must match the version file.

After the first publish, open the package's **Package settings → Change visibility**
and select **Public** so users can pull without signing in. Set `CODE_SERVER_IMAGE`
to the published name in `.env.example` once the repository name is chosen.

## Optional local backups

Set `BACKUP_LOCAL_PATH` in `.env` (default `./backups`), then include the local overlay:

```sh
docker compose -f compose.yaml -f compose.backup-local.yaml up -d
docker compose -f compose.yaml -f compose.backup-local.yaml logs -f backup
# Trigger an immediate backup:
docker compose -f compose.yaml -f compose.backup-local.yaml exec backup backup
```

Archives are stored in the host folder with names such as
`code-server-home-2026-10-21T03-00-00.tar.gz`. The schedule defaults to 03:00 in
`BACKUP_TIMEZONE`, using the same stop/archive/restart behavior as S3 backups.
S3 settings in `.env` are ignored by this overlay. Archives are not automatically
deleted; manage local retention yourself. See [Restoring a backup](docs/restore.md)
to restore directly from a local archive.

Use `compose.backup-local.yaml` for subsequent commands, including `down`. To
switch between local and S3 backups, run `up -d` with the chosen overlay; Compose
recreates the backup service for that destination. Include only one backup overlay.

## Optional S3 backups

Set the S3 variables in `.env`, then include the S3 backup overlay:

```sh
docker compose -f compose.yaml -f compose.backup.yaml up -d
docker compose -f compose.yaml -f compose.backup.yaml logs -f backup
# Trigger an immediate backup:
docker compose -f compose.yaml -f compose.backup.yaml exec backup backup
```

Backups run daily at **03:00 America/Chicago**, configurable through
`BACKUP_TIMEZONE` and `BACKUP_CRON_EXPRESSION`. The backup service uses the host
Docker socket to stop code-server while archiving the entire `/home` volume, then restart it.
Nested Docker data is excluded. Timestamped archives upload to an existing bucket;
configure bucket lifecycle rules for retention.

Backup stop labels and archive names are scoped to the Compose project. The
default S3 prefix is `<project>/home`. If multiple hosts share a bucket, set a
unique `BACKUP_ID` on each host; it controls archive names and the default prefix.
An explicit `AWS_S3_PATH` still overrides the prefix.

`AWS_ENDPOINT` is a hostname without `https://`. `AWS_S3_PATH` sets the object
prefix; `AWS_S3_BUCKET_LOOKUP` controls addressing (`auto`, `path`, or `dns`).
For R2, use your account's R2 endpoint and S3 credentials; `auto` normally works,
or use `path` explicitly.

Use both `-f` options for subsequent commands, including `down`. To disable backups:

```sh
docker compose -f compose.yaml -f compose.backup.yaml rm --stop --force backup
```

Then return to base Compose commands. See the [backup reference](https://offen.github.io/docker-volume-backup/reference/)
for more options.

For recovery instructions, see [Restoring a backup](docs/restore.md). Restore into
a new volume and verify its contents before replacing the active home volume.

## Tests

With `just` installed, run these commands from the repository:

```sh
just                 # List commands
just build           # Build the local test image
just check           # Check shell syntax and Compose configurations
just test            # Build once and run all local tests
just test-proxy      # Build and run only the proxy tests
just test-smoke      # Build and run only the startup/recovery tests
just test-backup     # Build and run only the backup/restore tests
just actions         # Run the GitHub Actions test job locally
just actions-list    # List workflow jobs without running them
```

Override the local image tag with `just image=my-image:test test`. Test recipes
use isolated containers and temporary storage. CI runs the same scripts from
`build/tests/`, which can also be run directly, for example
`bash build/tests/smoke.sh code-server-cloudflare:test`.

`just actions` downloads the pinned, checksum-verified `act` into a temporary directory, runs the native amd64
or arm64 test job as a pull request (without publishing), and removes the binary
afterward. Docker images and caches remain. Requires Linux, Bash, curl, and tar; initial tests need Internet access.
The local runner bind-mounts the repository and a temporary test directory at
matching host paths so sibling Docker containers can access test files and archives.

The smoke test needs privileged Docker and Internet access for initial Homebrew
installation. The restore test uses temporary volumes and local archives, without S3.

## Dependency updates

Code-server packages are verified against `build/code-server.sha256`. When updating `build/code-server.version`, update both architecture checksums from the upstream release metadata.

Homebrew's installer commit is pinned in `build/homebrew-install.version`, with its checksum in `build/homebrew-install.sha256`. The Docker build downloads and verifies the script, then bundles it at `/usr/local/lib/homebrew-install.sh`. Startup runs that bundled script as `cloud-user` after `/home` is mounted; Homebrew itself still requires Internet access on first installation. Update both pin files together and rebuild to change the installer.

Actions and service images are pinned to immutable revisions; update those pins deliberately and run the integration suite. The temporary act download is pinned and checked against `build/tests/act.sha256`. These pins do not freeze apt packages, Homebrew packages, or all transitive downloads.

Release jobs push each native architecture image after its tests pass, then assemble the recorded registry digests into the published multi-platform image without rebuilding.
