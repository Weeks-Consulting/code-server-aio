# AGENTS.md

Guidance for AI agents and contributors working in this repository. Record decisions
that are intentional but look wrong from the outside, so they are not "fixed" or
re-flagged in reviews.

## Project overview

<!-- TODO: One or two sentences on what this project is and who runs it. -->

A single container running code-server behind Envoy, which validates Cloudflare Access
JWTs, with nested Docker (Docker-in-Docker) for developers.

## Layout

<!-- TODO: Expand as the layout changes. -->

- `build/Dockerfile`, `build/runtime/`: image build and in-container services
  (Supervisor, Envoy, code-server).
- `build/tools/`: install, restore, and dependency tooling.
- `build/tests/`: unit and integration tests.
- `build/ci/`, `.github/workflows/`: release, publishing, and promotion.
- `compose*.yaml`, `manage`, `manage.py`: deployment, lifecycle, and backups.
- `docs/`: operator and maintainer guides.

## Intentional design decisions

Do not change these without discussing it first.

### Image packages (`build/Dockerfile`)

- **`build-essential` is required.** Developer-installed tools such as Homebrew compile
  from source and need a toolchain. Do not remove it to reduce image size.
- **`docker-cli` and `docker.io` are both listed.** `docker.io` only recommends
  `docker-cli`, and apt runs with `--no-install-recommends`, so `docker-cli` must be
  named explicitly.
- **`ENV HOME` is not set image-wide.** Root processes (supervisord, health checks,
  `docker exec`) would otherwise load `cloud-user`'s Python user site-packages. Docker
  derives `HOME` from `/etc/passwd`, and Supervisor sets it for code-server.
- **`apt-get upgrade -y` is intentional.** Packages are patched frequently, and the weekly
  rebuild of the current stable release is meant to pick those patches up. This trades
  build reproducibility for security.

### Container privileges

- Nested Docker is a hard requirement. The container does not use `privileged: true`;
  it uses `SYS_ADMIN`, `NET_ADMIN`, and unconfined seccomp/AppArmor/system paths.
  Unlike privileged mode, this does not expose host devices.
- Do not require `/dev/fuse`; some VM hosts do not provide it, preventing deployment.
- The entrypoint remounts `/sys/fs/cgroup` read-write so nested containers can start.
  Use `remount,bind,rw` to change only this mount's flags; a filesystem-wide remount
  can be denied in a user namespace. A failed remount must stop startup.
- `cloud-user` has passwordless sudo and Docker group access by design.

### Port forwarding

- code-server's `/proxy/<port>/` and `/absproxy/` are intentionally enabled. They pass
  through the same Envoy JWT checks as the IDE. Do not block or reroute them.

### Access lifetime and signing keys

- Keep the two-hour absolute stream lifetime and one-hour idle timeout (Envoy applies
  both to every stream, including WebSockets). Tokens are verified when a connection
  opens; the cap bounds how long an existing session outlives revocation.
- Keep synchronous JWKS refresh and fail closed after the one-hour key cache expires.
  Do not switch to asynchronous refresh that can retain stale keys during outages.
- Normal container health must work offline. The optional
  `container-healthcheck --access` probe checks online JWKS reachability separately; it does not prove user
  authorization. cloudflared waits for normal container health before starting.
- Health probes ignore workspace curl, Docker client, and Python user configuration
  and target the nested Docker daemon's Unix socket.
- Envoy admin uses its own mode 600 Unix socket. The workspace user should not have
  direct access to it, even though passwordless sudo intentionally permits root access.

### Resource use and backups

- `manage configure` caps workspace memory at 80% of the active Docker daemon host's
  reported total, including Docker Desktop/remote contexts. Leave CPU unlimited.
- cloudflared has a fixed 256 MiB memory cap. Do not invent a workspace PID cap
  without sizing it for developer processes and nested containers.
- Backups default to seven-day retention, scoped to the deployment's filename
  prefix. `BACKUP_RETENTION_DAYS=-1` explicitly disables pruning. S3 credentials
  need list/delete permissions when pruning is enabled.
- Local backup directories must already exist on the daemon host, with the expected
  owner, mode `700`, and a `.code-server-aio-backups` marker. The sidecar checks those
  conditions before copying and pruning. It sets permissions and ownership only on
  new archives.
- The backup sidecar intentionally uses the host Docker socket to stop/restart
  the workspace. Its `:ro` mount is not a Docker API permission boundary.
- Manual backups execute inside the running backup sidecar so they share its
  filesystem lock with scheduled runs. Do not use a separate `compose run`
  container for manual backups; its private lock permits overlapping archives.
- Managed lifecycle commands hold the running sidecar's Offen lock while
  changing containers. Start the workspace before starting a new sidecar so a
  scheduled backup cannot stop it while the lifecycle command is still starting it.
  A sidecar that is not running (for example, crash-looping) cannot be running a
  backup, so lifecycle commands continue without its lock and print a warning.
- Mutable release tags and weekly security rebuilds are intentional. Installers
  preserve existing digest pins; `manage upgrade` deliberately replaces them. Only the
  current stable release is rebuilt; older version tags stop changing, so
  `configure --pull` and `start --pull` warn when the configured tag is not the latest.
- Publishing bypasses the `runtime` build stage cache to refresh Debian packages.
  Checksum-pinned code-server and Envoy download stages remain cacheable; do not
  restore a global cache bypass that needlessly downloads unchanged artifacts.
- Scheduled and branch-requested rebuilds dispatch a `workflow_dispatch` run on the
  latest stable tag instead of building directly. Provenance records the run's own
  ref, so this is what makes rebuilt images attest to their tag. Rebuilds therefore
  use the tag's workflow and files, not `main`'s; that is intended.
- Version tag pushes publish prereleases and versioned images after testing. Only
  the manual promotion workflow makes a release stable/latest, reusing its image
  digest without rebuilding. Serialize promotion with image publication.
- Upload installer assets to a draft before publishing a new release. Reuse
  uploaded assets and resume missing uploads only if they match the release
  checksums. Bundle metadata is deterministic so retries can reproduce asset bytes.
- PR and `main` push runs never publish, so they skip documentation-only changes
  and newer commits cancel them. Tag, schedule, and manual runs always complete.

### Host tooling and cleanup

- `manage configure` creates/recreates containers and saves a private resolved Compose
  configuration. Plain `start` and `stop` only resume/stop those containers;
  `start --pull` refreshes images and recreates containers from the saved configuration.
  They do not apply changes to `.env` or Compose files. Preserve the saved backup mode and project
  name on reconfiguration unless explicitly overridden. Reject a project name change
  while the saved project has running containers or volumes on the active daemon.
- Configure takes the snapshot lock before the deployment `.env.lock`, which it
  holds through Compose rendering. The backup-key helper inherits that lock.
- Do not add daemon-wide `just stop` or `just cleanup` commands. Each command must
  clean up its own temporary files, containers, networks, and volumes; preserve
  deployment volumes, built images, and reusable build cache.
- Commands clean up temporary files on any catchable interruption: SIGINT, SIGHUP,
  and SIGTERM unwind like Ctrl-C so `finally` blocks run. Python entry points map
  SIGHUP/SIGTERM to `SystemExit`; shell scripts trap them. Keep signals the caller
  already ignores (`nohup`) ignored. Lifecycle Compose commands keep their own
  forward-once handling inside the reservation.
- `just run` uses a separate Compose project derived from the checkout's physical path.
  Keep the project stable so development volumes persist. Refuse to start or clean up
  an existing project. Identify the project by Compose's project label alone, so any
  container or network in it blocks a start; the run-mode label distinguishes development
  containers in listings.
- End users need no GitHub account, token, or GitHub CLI. The bootstrap, `manage
  install`, and `manage upgrade` check every downloaded file against the release's
  `SHA256SUMS` (fetched over HTTPS) before execution or replacement; never skip or
  weaken that check. Release attestations are not verified by the installers: the
  maintainer is the only account holder and accepts GitHub as the trust anchor. CI
  still attests images and `SHA256SUMS` for optional manual verification.
- Installers never install host packages. They check for Docker Compose and fail
  with instructions; host `gpg` is only needed (and checked) for restores.
- Releases ship deployment files as one flat `code-server-aio.tar.gz` asset, read
  in memory after its checksum matches. `SHA256SUMS` covers only the bundle, the
  bootstrap's `manage.py`, and `install.sh`, listed under their flat asset names so
  `sha256sum -c SHA256SUMS` works on downloaded assets.
- The default install directory is `~/code-server`, or `/opt/code-server` as root.
- `manage upgrade` installs the new release's files, replaces digest pins, and
  pulls, recreates, and starts the deployment through the configure path. It holds
  the snapshot lock across installation and configuration. Only after start does it
  try to remove the previous workspace image by ID, leaving other tags and images
  in use protected. It refuses to run in a source checkout.
- Restores reserve one named container per volume across the whole operation.
  Stop active helpers before staging cleanup, and retain staging/rollback after
  an interrupted replacement. A stale reservation after SIGKILL requires inspection.
  Reject a new restore while staging from a previous restore remains. Operators
  inspect and remove the reported staging directories before retrying.
- S3 listing and restore use the saved backup settings with the host AWS CLI.
  Stream remote archives into the standard restore staging directory and wait for
  transfer, decryption, and extraction to succeed before touching original files.
  Save the verified rollback in the deployment directory for remote restores.
- Lifecycle commands share the restore reservation name. Use the invocation's
  owner label to resolve interrupted acquisition and remove by container ID.
  Retain and report the reservation if a Docker lifecycle operation may still be running.
- Lifecycle commands pull images with visible progress before taking the reservation
  or Offen lock; the reservation is created with `--pull never`. Compose children run
  in their own session and receive one forwarded signal; the reservation is released
  if Compose exits by itself and retained if a second signal or the 60-second bound
  abandons it, or if Compose itself was terminated by a signal (Docker may still be
  acting on its request).
- `.env` validation treats backslash as an escape inside single quotes too, because
  Compose's dotenv parser does (`KEY='C:\dir\'` is unterminated to Compose).

<!-- TODO: Add further intentional decisions here, one heading per area. -->

## Out of scope for reviews


<!-- TODO: Add other findings that reviewers should skip, with a one-line reason. -->

## Commands

<!-- TODO: Confirm and extend. Commands below come from the justfile. -->

- `just check`: shell syntax, shellcheck, unit tests, Compose configs.
- `just test`: check plus proxy, smoke, and backup integration tests.
- `just run`: build and run the checkout's development project, attached to logs.
- `just test-smoke`: full-container startup test; needs a Docker host that allows
  `SYS_ADMIN` and unconfined seccomp/AppArmor/system paths.

## Conventions

<!-- TODO: Code style, comment density, naming, commit message rules, PR expectations. -->

## Security notes

<!-- TODO: Threat model, trust boundaries, secrets handling, what Access users can do. -->

## Known issues and follow-ups

<!-- TODO: Track known issues and follow-ups here. -->
