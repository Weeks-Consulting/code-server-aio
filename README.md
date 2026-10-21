# All-in-One Development Environment with Cloudflare Access

Deploy a complete, browser-based development environment with code-server and Docker-in-Docker using Docker Compose. Run it on a Docker host and access it remotely through Cloudflare Tunnel, with authentication enforced through Cloudflare Access policies and local token validation.

## AI disclosure

AI tools assisted with the development, documentation, and review of this project.
The entire project has also been reviewed and tested by a real person.

## Setup

1. Copy `.env.example` to `.env` and set:
   - `CODE_SERVER_IMAGE`: the published GHCR image (replace the placeholder owner/repository).
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

## Access protection

Traffic flows through cloudflared → HAProxy (`:8080`) → code-server (`127.0.0.1:8081`).
HAProxy validates Access JWT signatures, issuer, audience, and timestamps.

- **403:** Missing or invalid token, including an expired session or incorrect AUD.
- **503:** Invalid/missing Access settings, initial key download pending, or cached keys older than 24 hours.

Signing keys refresh hourly, with retries every minute on failure. Startup blocks
access until keys are fetched. The health check verifies supervised services,
nested Docker, code-server, and HAProxy's rejection of unauthenticated requests.
Cloudflared starts alongside the container so HAProxy can show its 503 setup page.
Customize the error pages in `build/proxy/errors/` and rebuild.

## Operation and customization

```sh
docker compose logs -f
docker compose exec --user cloud-user code-server bash
docker compose exec code-server supervisorctl status
docker compose down
```

code-server runs as `cloud-user` (UID/GID 10000), with passwordless sudo and Docker
group access. Supervisor manages the services; the container runs privileged for
Docker-in-Docker. Startup repairs ownership of the home directory and `/workspace`.
If a critical service exhausts its startup retries, Supervisor shuts down so
Docker's restart policy can restart the container. Missing Access configuration
reports unhealthy and keeps access blocked until corrected.

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

Set `REPAIR_HOME_OWNERSHIP=false` to skip recursive ownership repair on large home
directories. The home and workspace directory owners are still corrected.

If reusing the previous home-volume layout, move its existing contents into a
`cloud-user/` subdirectory before restarting with this mount. Ownership repair
remains scoped to `/home/cloud-user`, preserving ownership of other home directories.

Add packages in `build/dependencies/*.txt`, one-time startup commands in `build/startup/entrypoint.sh`, and
foreground services in `build/supervisor/supervisord.conf`.
Dependency lists contain one Debian package name per line, grouped by subsystem.

Build files are grouped by purpose under `build/`: `startup`, `dependencies`,
`supervisor`, `proxy`, `health`, `homebrew`, `backup`, and `tests`. The Dockerfile
is `build/Dockerfile`; Compose files stay at the project root.

## Local development

Configure the Cloudflare variables in `.env` as above. Local builds do not require
`CODE_SERVER_IMAGE`. Set `CODE_SERVER_VERSION` in `.env` to the code-server release
you want to install; local Compose and `just` builds require it. Docker has no
default version. Direct Docker builds require `--build-arg CODE_SERVER_VERSION=...`.

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
a GitHub release with the matching tag, such as `v4.141.0`. CI uses this file;
local builds use the version specified in `.env`. CI rejects invalid versions
and mismatched release tags before building. Manually triggered runs on tags
also require a matching version. Pushing a tag alone does not publish.

After both architectures pass, the release publishes
`ghcr.io/<owner>/<repository>:4.141.0`, with provenance and an SBOM. Stable releases
also update `latest`; prereleases publish only the version tag. Image names are
derived from the repository and lowercased automatically. Publishing uses
`GITHUB_TOKEN`; no registry secret or naming variables are needed.

After the first publish, open the package's **Package settings → Change visibility**
and select **Public** so users can pull without signing in. Set `CODE_SERVER_IMAGE`
to the published name in `.env.example` once the repository name is chosen.

## Optional S3 backups

Set the S3 variables in `.env.example`, then include the backup overlay:

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
use isolated containers and temporary storage.

Run the GitHub Actions test job locally with Docker running:

```sh
bash build/tests/actions.sh
```

This downloads the latest `act` into a temporary directory, runs the native amd64
or arm64 test job as a pull request (without publishing), and removes the binary
afterward. Docker images and caches remain. Pass `--list` to list jobs without
running them. Requires Linux, Bash, curl, and tar; initial tests need Internet access.

Run JWT validation, key rotation, error-page, and WebSocket tests in isolation:

```sh
just build
docker run --rm --entrypoint /usr/bin/python3 \
  --mount type=bind,src="$(pwd)/build/tests/test_access_proxy.py",dst=/tmp/test_access_proxy.py,readonly \
  code-server-cloudflare:test /tmp/test_access_proxy.py
bash build/tests/smoke.sh code-server-cloudflare:test
bash build/tests/backup-restore.sh code-server-cloudflare:test
```

The smoke test needs privileged Docker and Internet access for initial Homebrew
installation. The restore test uses temporary volumes and local archives, without S3.
