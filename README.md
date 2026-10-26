# All-in-One Development Environment with Cloudflare Access

Deploy a complete, browser-based development environment with code-server and Docker-in-Docker using Docker Compose. Run each instance on its own dedicated VM, which is the isolation boundary, and access it remotely through Cloudflare Tunnel, with authentication enforced through Cloudflare Access policies and local token validation.

See [Install](#install) for published releases, [Build](#build) for development and
the complete [Just command list](#just-commands), and [Deploy](#deploy) to run services.

## AI disclosure

AI tools assisted with the development, documentation, and review of this project.
The entire project has also been reviewed and tested by a real person.

## Install

Supported hosts are Debian Linux and macOS. Start with a working Docker
installation and Python 3. The deployment installer checks for GnuPG and
Docker Compose, and installs missing runtime packages using apt on Debian or
Homebrew on macOS. It also installs missing CA certificates on Debian. Package
installation uses sudo on Debian when needed; Homebrew must already be available
on macOS. The installer downloads a published image configuration, so no local
build tools or source checkout are needed.

On macOS, keep the deployment, checkout, and backup archives in a folder shared
with Docker, such as your home directory. Use `--directory "$HOME/code-server"`
without `sudo` as shown below.

Installation and management run through `manage`, a thin shell wrapper around
`manage.py` using Python's standard library. From a checkout, install with:

```sh
./manage install --directory "$HOME/code-server"
```

For hosts without a checkout, the compatibility bootstrap needs curl and Python 3.
It downloads the latest released `manage.py`, which performs the installation:

```sh
curl -fsSL https://raw.githubusercontent.com/Weeks-Consulting/code-server-aio/refs/heads/main/build/tools/install.sh | sudo sh -s --
```

Use `--directory PATH` to choose the installation directory. `sudo` is optional
when the directory is owned and writable by your current user. If it does not
exist, its parent must be writable so the installer can create it. For example:

```sh
curl -fsSL https://raw.githubusercontent.com/Weeks-Consulting/code-server-aio/refs/heads/main/build/tools/install.sh | sh -s -- --directory "$HOME/code-server"
```

For this example, edit `$HOME/code-server/.env` with your usual editor and launch
with `"$HOME/code-server/manage" --pull`. You can combine `--directory`
with `--version` to select both the location and release.

Preview the dependency commands and deployment changes before installing:

```sh
./manage install --dry-run --directory "$HOME/code-server"
```

`manage install --dry-run` installs no packages, makes no network requests, and leaves the
deployment untouched. It reports existing files that would be backed up and
whether `.env` would be created or preserved, without displaying its secrets.
Without `--version`, it reports that the latest release would be resolved during
installation. Omit `--dry-run` to apply the changes. On macOS, run without sudo
so Homebrew can install missing dependencies.

The installer creates `/opt/code-server`, downloads `compose.yaml` and the optional
S3 and local backup overlays from the latest stable GitHub release, and creates a `.env`
with the GHCR image tagged `:latest`, or the version supplied with `--version`. New releases publish the installer as a
release asset and include `SHA256SUMS`; the installer verifies downloaded deployment
files before replacing any files. Older releases without checksums produce a warning.
Checksums detect mismatched downloads; GitHub and the release publisher remain
the trust anchor. Rerunning the installer refreshes the
Compose files, saving their previous contents as `.bak` files. Existing `.env`
values and comments are preserved except for `CODE_SERVER_IMAGE`, which is set to the selected tag unless it contains a digest pin; missing settings are appended with notes from
the new release. `.env` is readable only by its owner. If you run the installer
with `sudo`, you must also run `manage` with `sudo`. Otherwise, run `manage`
as the same user that installed the deployment. This applies to all launcher
commands, including updates, backups, and restores, and to direct Docker Compose
commands that read this `.env`.
The installer does not start
or restart services; start them with Docker Compose after configuring `.env`.
All services use `restart: unless-stopped`: once deployed, Docker restarts them
when they exit or the Docker daemon restarts, unless you explicitly stop them.

To refresh deployment files, run `manage install --directory PATH` with the deployment
directory and any desired `--version` or `--repository` option. Review any
new `.env` settings before restarting. The installer sets `CODE_SERVER_IMAGE` to `:latest` or the version supplied with
`--version`, preserving existing digest pins with a warning. Then run
`sudo /opt/code-server/manage --pull` with your usual launch options.
For an installation without `sudo`, run the launcher as the installing user
without `sudo`, using that installation's path.

To install a specific release, pass its full tag, including the leading `v`:

```sh
./manage install --version v4.141.0 --directory "$HOME/code-server"
```

For a project revision, use `--version v4.141.0-1` instead.
Use `--directory /another/path` to change the destination.
The release's image must have finished publishing before you start the deployment.

```sh
cd /opt/code-server
sudoedit .env
```

## Build

Use a source checkout with a working Docker installation including Compose and
Buildx. Run all Just commands from the checkout, including from zsh.

### Just commands

All available recipes are listed here:

```sh
just                 # List commands
just install         # Install host build and test dependencies
just version         # Print repository dependency versions without hashes (offline)
just outdated        # List only outdated pins without changing files
just upgrade         # Look up latest versions, then confirm updates
just build           # Build the local test image
just check           # Check shell syntax and Compose configurations
just test            # Build once and run all local tests
just test-proxy      # Build and run only the proxy tests
just test-smoke      # Build and run only the startup/recovery tests
just test-backup     # Build and run only the backup/restore tests
just actions         # Run the GitHub Actions test job locally
just actions-list    # List workflow jobs without running them
```

`just install` prepares a development machine using Homebrew on macOS or apt on
Debian. It does not install or launch a deployment; use [Install](#install) for
published releases and [Deploy](#deploy) to start services.

Builds read the code-server version from `build/code-server.version`. Building the
local test image needs no `.env` or Cloudflare credentials.

Override the local image tag with `just image=my-image:test test`. Test recipes
use isolated containers and temporary storage. CI runs the same scripts from
`build/tests/`, which can also be run directly, for example
`./build/tests/smoke.sh code-server-cloudflare:test`.

### Test details

`just test-proxy` checks the real Envoy binary against disposable HTTPS signing keys
and a Unix-socket fixture: JWT claims, duplicate headers, allowlists, TLS validation,
key rotation and outages, error pages, and WebSocket lifetime limits. It also starts
the production Compose topology with real code-server and a temporary JWKS service,
then checks socket isolation from a client on both networks, Envoy hardening, backend
and sidecar restarts, and rejection of requests while either service is unavailable.
No Cloudflare account or published host ports are needed.

`just test-backup` exercises both local archives and S3 uploads to a disposable,
digest-pinned RustFS server. The S3 test checks application stop/restart, the object
prefix and filename, encryption, and a restore with numeric ownership preserved.
It uses temporary credentials and storage with no published ports or real S3 account.
Run it directly with `./build/tests/backup-s3.sh code-server-cloudflare:test`.

`just actions` downloads the pinned, checksum-verified `act` into a temporary directory, runs the native amd64
or arm64 test job as a pull request (without publishing), and removes the binary
afterward. Docker images and caches remain. Supports Debian Linux and macOS on
amd64/x86_64 and arm64. Initial tests need Internet access.
The local runner mounts the repository and temporary test storage at paths
also accessible to sibling Docker containers.
On macOS, it uses the active Docker CLI endpoint for the host connection and
`/var/run/docker.sock` inside the Linux runner. Temporary test storage uses a
disposable Docker volume to preserve Linux ownership. The local run uses the
mounted checkout without a GitHub token and disables GitHub cache and build-record
uploads; GitHub-hosted runs keep those features.

The smoke test needs privileged Docker and Internet access for initial Homebrew
installation. On macOS it uses named volumes to preserve Linux UID/GID ownership;
on Linux it also exercises an empty home bind mount. Temporary bind-mounted test
files are created in ignored `.test-tmp.*` directories under the checkout, which
must be shared with Docker. The restore test uses temporary volumes and encrypted
local archives, without S3.

Host scripts are checked as POSIX `sh` and tested on Debian and macOS in CI.
Bash is still used to check the container's Linux runtime scripts and CI release
logic; the version bundled with either host is sufficient.

### Dependency updates

`just version` reports repository versions without hashes, including code-server, act, the Homebrew installer, GitHub Actions, Envoy, cloudflared, and the backup image.

`just upgrade` requires Python 3 and Internet access. It prints current and latest upstream pins and asks `[y/N]` before changing files. It updates code-server, act, and Envoy to the latest stable GitHub releases, including code-server and act checksums for every supported architecture, the Homebrew installer commit and checksum, GitHub Actions commit pins and version comments, and service, Debian build, CI, and restore image digests on their existing tag tracks.
Homebrew installer updates include a compare link before confirmation. Files are
staged before replacement; write failures attempt to restore previous contents. A
process or host crash during replacement can still require recovery using Git. GitHub asset SHA-256 metadata is used when available; otherwise assets are downloaded and hashed. Set `GH_TOKEN` or `GITHUB_TOKEN` if needed for GitHub API rate limits. Lookup failures and a declined or unanswered prompt leave files unchanged. Review `git diff` and run `just test` afterward; rebuilding/redeploying is a separate step.

GitHub Actions remain pinned to full 40-character commit SHAs in `uses:`. During `just upgrade`, the latest stable release of each Action is resolved to its commit SHA; approval updates that hash and the adjacent release-version comment in every workflow occurrence. The version comment is informational; the commit SHA selects the code that runs. Actions are included in the same confirmation prompt as the other dependency upgrades.

`just version`, `just outdated`, and `just upgrade` hide commit SHAs and image digests by default; the Homebrew installer is labeled as a pinned commit because it has no release version. `just outdated` checks the same upstream sources as `just upgrade`, lists only outdated dependencies with their current and latest versions, and reports when all pins are current. It never prompts or changes files, and skips package/installer downloads needed only for checksum updates. It requires Python 3 and Internet access. A completed check exits successfully whether or not updates are available; lookup failures exit with an error.

Add `--hashes` to any dependency command to include commit SHAs and image digests: `just version --hashes`, `just outdated --hashes`, or `just upgrade --hashes`. Hash changes are still detected and reported as available updates when hashes are hidden.

Code-server packages are verified against `build/code-server.sha256`. When updating `build/code-server.version`, update both architecture checksums from the upstream release metadata.

Homebrew's installer commit is pinned in `build/homebrew-install.version`, with its checksum in `build/homebrew-install.sha256`. The Docker build downloads and verifies the script, then bundles it at `/usr/local/lib/homebrew-install.sh`. Startup runs that bundled script as `cloud-user` after `/home` is mounted; Homebrew itself still requires Internet access on first installation. Update both pin files together and rebuild to change the installer.

Actions and service images are pinned to immutable revisions; update those pins deliberately and run the integration suite. The temporary act download is pinned and checked against `build/tests/act.sha256`.

## Deploy

Complete [Setup](#setup) and [Environment configuration](#environment-configuration), then run `sudo /opt/code-server/manage --pull`
for the default installation with `sudo`.
Releases that predate the launcher can use `sudo docker compose up -d --pull always`
from `/opt/code-server`. To enable backups, configure the S3 settings and include
`-f compose.yaml -f compose.backup.yaml`, or use `-f compose.yaml -f compose.backup-local.yaml`
for local-folder backups as described below.

### Launch options

Run `./manage` from a checkout, or `sudo /opt/code-server/manage` for
the default installation with `sudo`. For an installation without `sudo`, run
`/path/to/installation/manage` as the same user that installed it.
Keep `manage` in the same directory as `compose.yaml`.
The launcher always uses `.env` and Compose files beside itself, regardless of
your current working directory. It starts services in the background by default
and is included by the installer
for releases that contain it. Installed deployments include only runtime Compose
files, the launcher, the restore helper, and `.env`; build files and source code stay in the checkout.

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

Stop the deployment, then restore a local or downloaded archive into its original
home volume (replace `code-server_code-server-home` with its actual name):

```sh
./manage restore --archive /path/to/backup.tar.gz.gpg --volume code-server_code-server-home
```

Relative archive paths resolve from your current working directory. Restore
requires an existing volume with no running containers attached. Before replacing
its contents, it saves a rollback `.tar.gz.gpg` (or `.tar.gz` when no key is supplied) in a private `pre-restore.*` directory
beside the input archive and prints the path. The rollback file and directory belong
to the effective user running `manage`, including root when run as root. A root helper
container preserves archived numeric UID/GID values when restoring the volume.
Restart with your usual launch options.
Encrypted restores require `gpg` and `python3` on the Docker host and the original key in `.env`
(or exported as `GPG_PASSPHRASE`). Legacy unencrypted archives still work.
Follow [the restore guide](docs/restore.md) for recovery steps. The
installer includes the restore helper for releases that contain it.

### Deploy a local build

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

## Setup

Anyone granted Cloudflare Access to this privileged development environment
effectively has root access to the Docker host. Restrict the Access policy to
specific trusted users. A Service Auth token also grants root access to the VM;
protect it as a root credential. For defense in depth, set the optional origin
allowlists below in addition to restricting the Access policy.

1. Edit the installer's `.env` (or copy `.env.example` when using a checkout) and set:
   - `CODE_SERVER_IMAGE`: the published GHCR image (selected by the installer; defaults to the project image in a checkout).
   - `CLOUDFLARE_TUNNEL_TOKEN`: your remotely managed tunnel token.
   - `CF_ACCESS_TEAM_DOMAIN`: your `team.cloudflareaccess.com` hostname, without `https://`.
   - `CF_ACCESS_AUD`: the 64-character Application Audience tag copied exactly from your Access application, including letter casing.
2. Configure the tunnel origin as `http://envoy:8080` and protect the hostname
   with an Access application and an Allow or Service Auth policy.
3. Start the services:

   ```sh
   sudo /opt/code-server/manage --pull
   ```

   This command assumes the default installation with `sudo`. If you installed
   without `sudo`, run `/path/to/installation/manage --pull` as the same
   user that installed it. From a checkout, use `./manage --pull`.

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
| `CODE_SERVER_IMAGE` | Optional; `ghcr.io/weeks-consulting/code-server-aio:latest`. | Full registry image reference to pull, such as `ghcr.io/weeks-consulting/code-server-aio:4.141.0` or `:4.141.0-1`. The installer sets this to `:latest` by default or the requested version on every installation, preserving existing digest pins. `latest` follows stable releases. Ignored when `compose.build.yaml` selects the local image. |
| `CODE_SERVER_VERSION` | Optional; `build/code-server.version`. | Overrides the upstream code-server version for local builds, such as `4.141.0`, without a leading `v`. Used only by `compose.build.yaml`; it does not change a prebuilt image. The package must have a checksum in `build/code-server.sha256`, so normally update `build/code-server.version` instead. |
| `CLOUDFLARE_TUNNEL_TOKEN` | Required; no default. | Tunnel token for your remotely managed Cloudflare Tunnel, used by the cloudflared sidecar. Configure the tunnel's origin service as `http://envoy:8080`. This is the tunnel token, not a Cloudflare API token or an Access service token. |
| `CF_ACCESS_TEAM_DOMAIN` | Required for access; no default. | Cloudflare Access team hostname, such as `my-team.cloudflareaccess.com`. Supply only the hostname, without `https://`, a path, or a trailing slash. Envoy fetches signing keys from this team and checks the JWT issuer. Missing or invalid configuration prevents the Envoy sidecar from starting. |
| `CF_ACCESS_AUD` | Required for access; no default. | The Access application's 64-character hexadecimal Application Audience (AUD) tag. Audience matching is case-sensitive: copy the value exactly as Cloudflare provides it, without changing letter casing. Use the application protecting this service, not the tunnel ID or account ID. Tokens with a different audience receive HTTP 403; missing or malformed configuration prevents Envoy from starting. Find it under **Zero Trust → Access controls → Applications → Configure → Additional settings**. |
| `CF_ACCESS_ALLOWED_EMAILS` | Optional; empty. | Comma-separated exact, case-sensitive email claims, for example `alice@example.com,bob@example.com`. |
| `CF_ACCESS_ALLOWED_SERVICE_TOKENS` | Optional; empty. | Comma-separated exact `common_name` claims from Access service-token JWTs. If either allowlist is set, the signed token must match an email OR a service identity; missing claims and spoofed headers do not match. Invalid list syntax stops startup. Both empty delegates identity authorization to the Access policy. |
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
| `BACKUP_LOCAL_PATH` | Optional; `./backups`. Local overlay only. | Host folder receiving `.tar.gz` archives. Relative paths resolve beside the Compose files, so the installer's default is `/opt/code-server/backups`. Use an absolute path such as `/mnt/backups/code-server` to select another location. Compose creates the folder if it does not exist and mounts it at `/archive` in the backup container. S3 settings are not passed to the local backup container. |
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

Traffic flows through cloudflared → Envoy (`:8080`) → code-server over
`/run/code-server/backend.sock`. Configure the remotely managed tunnel origin as
`http://envoy:8080`. Code-server has **no TCP listener** and no published ports.
Only code-server and Envoy mount the socket volume; Envoy mounts it read-only.
Cloudflared and backup containers have no socket mount. Code-server uses a separate
`workspace` network for outbound development traffic; Envoy and cloudflared share
an `edge` network. Containers on either network cannot bypass JWT validation by
connecting to a code-server TCP port. The host administrator and containers with
host Docker API access remain trusted and can override this isolation.

Envoy uses the official, digest-pinned `envoyproxy/envoy` image directly in Compose.
It runs as the image's unprivileged `envoy` user, with supplemental group 10000
for socket access, all capabilities dropped, no privilege escalation, and a private
temporary directory. Compose provides its bootstrap with mode 0444, and the backend
socket volume is mounted read-only. The container root filesystem
is writable because Compose injects inline configs by copying them into the container. The application image
contains no Envoy binary. Envoy has no home-volume or Docker-socket mount, and its
admin interface listens only on its own loopback address, port 9901.

Compose's native `configs.content` interpolation renders the bootstrap defined in
`compose.yaml` and mounts it read-only at `/etc/envoy/envoy.json`. This requires
Docker Compose 2.23.1 or newer. There is no configuration container, renderer script,
key-refresh helper, PEM conversion, or periodic proxy reload.

Envoy's native JWT filter checks signatures, issuer, audience, required expiration,
and not-before timestamps with one second of clock tolerance; RBAC restricts the
verified algorithm to RS256. Small inline Lua filters validate startup settings,
reject duplicate or missing JWT headers, and match the optional literal email and
service-token allowlists against verified claims. Invalid settings prevent Envoy
from starting. Cookies, bearer tokens, and query parameters are not accepted as
substitutes for `Cf-Access-Jwt-Assertion`.

Envoy downloads JWKS over HTTPS with CA and team-hostname verification when a worker
first needs keys, and caches them for one hour per worker. After cache expiry, a
request must fetch fresh keys successfully before it can authenticate. Failed fetches
are retried twice; requests stay blocked if no unexpired keys are available. Requests
can continue using still-valid cached keys during an outage. Asynchronous refresh is
intentionally disabled because it can retain stale keys during a prolonged outage.
A newly introduced signing key may be rejected until the current cache expires.

- **403:** Missing, duplicated, malformed, expired, or otherwise invalid token; incorrect AUD; denied identity; or signing keys unavailable when verification needs a fetch.
- **503:** A valid authenticated request reaches Envoy while workspace setup is running or the backend is unavailable.
- **Connection unavailable:** Envoy is stopped or its environment configuration is missing/invalid. Check `docker compose logs envoy`.

The 403 and 503 pages include troubleshooting steps; the 503 page retries every five
seconds. Customize `local_reply_config` in the `envoy-bootstrap` config in `compose.yaml`
to change the error pages.

WebSocket tokens are checked when the connection opens. Idle streams close after
one hour, and all streams have an absolute two-hour lifetime, independent of key
refresh success. Open sockets are not revalidated when their JWT expires. To revoke
access immediately, remove the user or service token from the Access policy, revoke
its Access sessions, then run `docker compose restart envoy` with the deployment's
usual Compose options to disconnect existing sockets. Apply origin allowlist changes
with `manage start`, which recreates Envoy with its updated environment and bootstrap.

Envoy starts alongside workspace setup, so authenticated visitors can see the startup
page while Homebrew installs. The code-server health check verifies completed workspace initialization, running
supervised services, nested Docker, and the backend Unix socket. The separate Envoy health check
verifies listener readiness and rejection of unauthenticated requests; it does not
prove that a JWKS fetch will succeed or that a particular user can authenticate.
Initialization failures retain the Homebrew retry marker and restart code-server.

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
Docker's restart policy can restart the container. Missing or invalid Access
configuration prevents Envoy from starting until corrected.

Save projects under `/home/cloud-user`; `/workspace` is not provided.

Named volumes persist all of `/home` (user files, settings, extensions, and shared
installations such as `/home/linuxbrew`) and
`/var/lib/docker`. `docker compose down` keeps them; `down -v` deletes them.

[Homebrew](https://docs.brew.sh/Homebrew-on-Linux) installs as `cloud-user` on first
startup under `/home/linuxbrew/.linuxbrew`, after mounts are available. This also
works with an empty `/home` bind mount. Initial setup needs Internet access and may
take several minutes; later starts reuse the installation without updating it.
`brew` is available in `cloud-user` terminals and login shells; root login shells
skip its user-controlled initialization. Run `brew update` explicitly
when needed. Installation is limited to 600 seconds by default; set
`HOMEBREW_INSTALL_TIMEOUT` to change it. Failure or timeout stops startup and
retains a marker so incomplete installations are retried.

Supervisor runs `initialize-workspace.py` to prepare the private socket directory and mounted
home directory, and call `preinstall.sh` to install prerequisites such as Homebrew.
Only successful initialization starts code-server, which Supervisor runs directly as `cloud-user`. Restarting the
code-server service does not repeat workspace initialization.

Envoy runs as a separate Compose service using the upstream image. Compose mounts its
interpolated bootstrap directly; changing Access settings with `manage start`
recreates Envoy.
Code-server starts with `--socket` and removes a stale socket before binding it;
restarting either service does not require restarting the other.

If reusing the previous home-volume layout, move its existing contents into a
`cloud-user/` subdirectory before restarting with this mount. Startup creates the home directory as needed
without changing ownership of its contents or other home directories.

Add apt packages in `build/Dockerfile`, one-time startup commands in `build/runtime/entrypoint.sh`, and
foreground services in `build/runtime/supervisord.conf`. Every supervised service is
critical: the health check requires it to be running, and repeated startup failure
restarts the container.

Files shipped in the application image live in `build/runtime/`, including startup
scripts and Supervisor configuration. Envoy configuration and error pages are
embedded in `compose.yaml`. Build-only version and checksum
files stay beside `build/Dockerfile`. Host installation and restore helpers live
in `build/tools/`; tests stay in `build/tests/`. Compose files stay at the project root.

## Image publishing

GitHub Actions builds and tests native amd64 and arm64 images on pull requests,
pushes to `main`/`master`, manual runs, weekly scheduled builds, and published
GitHub releases. Published releases and weekly scheduled builds upload images;
regular commits and manual runs run tests only by default. To publish an urgent
rebuild, use **Actions → Build and publish container → Run workflow** and enable
**Rebuild and publish the latest stable release**. This uses the latest stable
release source, bypasses the build cache, and applies the same tests and latest-release
checks as the weekly rebuild. Every Monday at 08:17 UTC
(03:17 in Chicago during daylight saving time, 02:17 during standard time), the
scheduled build checks out the latest stable GitHub release tag, uses the pinned
Debian base image and refreshes its packages, and bypasses cached package layers. It republishes
that release's version tag and `latest` only after both architectures pass all tests.
The build fails without publishing if no stable release exists or its tag is invalid.
If the latest release changes during testing, the older release receives only its
version tag; `latest` is kept for the current release. CI covers full startup, Homebrew persistence,
service recovery, and a local backup/restore round trip.

To publish, update `build/code-server.version` (for example, `4.141.0`) and publish
a GitHub release with the matching tag, such as `v4.141.0`. CI, `just build`, and
Compose builds all read this file. CI rejects invalid versions
and mismatched release tags before building. Manually triggered runs on tags
also require a matching version. Pushing a tag alone does not publish.

After both architectures pass, the release publishes
`ghcr.io/<owner>/<repository>:4.141.0`, by assembling the tested image digests. Tested images are uploaded by digest only;
no temporary test tags are published. The test jobs have read-only repository
permissions. On publishing runs, they transfer the exact tested images as one-day
artifacts to separate upload jobs with package write permission; images are not rebuilt.
These image artifacts use GitHub Actions artifact storage. This Docker-load publication path does not generate BuildKit provenance or SBOM attestations. Stable releases
also update `latest`; prereleases publish only the version tag. Stable version
tags receive weekly Debian dependency rebuilds, so they can point to new image
contents without a code-server version change. Pin an image digest if you need
fixed contents. To apply a rebuild, run `manage --pull` with your usual launch
options and `sudo` if you installed with `sudo`. This runs
`docker compose up --pull always`: it checks the registry even when the tag is
already available locally, and recreates containers when the pulled image changes,
preserving mounted volumes. This applies to both `:latest` and version tags such
as `:4.141.0-1`. A reference containing `@sha256:…` stays fixed to that digest;
change the reference to receive a rebuild. A plain container restart does not
pull images. Image names are
derived from the repository and lowercased automatically. Publishing uses
`GITHUB_TOKEN`; no registry secret or naming variables are needed.

For project-only fixes, keep `build/code-server.version` unchanged and publish a
stable GitHub release such as `v4.141.0-1`, then `v4.141.0-2` for the next fix.
The numeric revision must be at least 1, with no leading zeros. These releases
publish the exact image version (`4.141.0-1`), update `latest`, and are eligible
for weekly rebuilds. The base version must still match the version file.

To publish a test image, mark the GitHub release as a **pre-release**. It publishes
only its version tag and does not update `latest`. RC tag suffixes are not supported.

After the first publish, open the package's **Package settings → Change visibility**
and select **Public** so users can pull without signing in.

## Backup encryption

Both Offen backup overlays require GPG encryption and write `.tar.gz.gpg` archives.
The installer generates a random 256-bit `GPG_PASSPHRASE` in `.env` if it is missing
or empty, including when refreshing an existing installation. When using a checkout,
`manage --backup local` or `manage --backup s3` generates it before starting services;
manual backup commands also initialize it if necessary. Existing nonempty keys are
preserved. Generated keys are never printed, and `.env` is saved with mode `600`.
The initializer uses Python 3 and a persistent `.env.lock` sidecar to serialize
concurrent launches without replacing an existing key.
You can set your own passphrase in `.env` before installation or startup.

**Save `GPG_PASSPHRASE` separately in a password manager or another secure location.**
The home-volume backup does not include the host deployment's `.env`. Losing the key
makes encrypted archives unrecoverable. Keep old keys if you change the passphrase;
older archives still need the key used to create them. Restart the backup service
with your usual launch options after changing the key. Existing plaintext archives
remain unchanged.

For direct Compose use, first set a passphrase in `.env` or generate one with
`./build/tools/backup-key.sh .env` from a checkout (`./backup-key.sh .env`
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

For direct Compose commands, export `LOCAL_BACKUP_UID` and `LOCAL_BACKUP_GID`
with the desired host IDs; they default to `0:0` when the launcher is not used.
For example, `export LOCAL_BACKUP_UID=$(id -u) LOCAL_BACKUP_GID=$(id -g)` before
the commands below. With `sudo docker compose`, pass them explicitly:
`sudo env LOCAL_BACKUP_UID="$LOCAL_BACKUP_UID" LOCAL_BACKUP_GID="$LOCAL_BACKUP_GID" docker compose ...`.

Set `BACKUP_LOCAL_PATH` in `.env` (default `./backups`), then include the local overlay:

```sh
docker compose -f compose.yaml -f compose.backup-local.yaml up -d
docker compose -f compose.yaml -f compose.backup-local.yaml logs -f backup
# Trigger an immediate backup:
docker compose -f compose.yaml -f compose.backup-local.yaml exec backup backup
```

Archives are stored in the host folder with names such as
`code-server-home-2026-10-21T03-00-00.tar.gz.gpg`. The schedule defaults to 03:00 in
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

For recovery instructions, see [Restoring a backup](docs/restore.md). The restore
helper backs up the original volume before replacing its contents in place.

## Image verification

Release jobs push each native architecture image after its tests pass, then assemble the recorded registry digests into the published multi-platform image without rebuilding.
The publish job creates signed GitHub build provenance for the resulting index digest
and stores it in GHCR. Before deploying a new image, verify it using:

```sh
gh attestation verify oci://ghcr.io/weeks-consulting/code-server-aio:latest --repo Weeks-Consulting/code-server-aio --signer-workflow Weeks-Consulting/code-server-aio/.github/workflows/container.yaml
```

Use the verified digest in `CODE_SERVER_IMAGE` to deploy exactly those bytes. Tags
remain mutable to support weekly security rebuilds; digest pins require deliberate
updates. Installer reruns preserve existing digest pins and warn that they were kept.
`manage upgrade` deliberately replaces a digest pin with the latest release tag;
verify and set a new digest instead if you want to keep deploying verified bytes.
Verification requires `gh` and may require GHCR authentication; it is an
explicit deployment check. Restrict package publishing to trusted maintainers.
`manage start --pull` also warns when a version-tagged GHCR image differs from its repository's
latest stable release, with a best-effort check that stays quiet on network errors. Starts without
`--pull` skip the network check.

Release deployment checksums have GitHub artifact provenance. To verify their origin
with GitHub CLI before using them, download `SHA256SUMS` from the selected release and
run `gh attestation verify SHA256SUMS --repo Weeks-Consulting/code-server-aio`.
Checksums downloaded from the same release verify file integrity; independent
provenance verification also verifies the publishing repository and workflow.
Older releases without checksums remain installable with a warning.
