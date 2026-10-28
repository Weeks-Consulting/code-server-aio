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
- `compose*.yaml`, `manage.py`: deployment and backups.

## Intentional design decisions

Do not change these without discussing it first.

### Image packages (`build/Dockerfile`)

- **`build-essential` is required.** Developer-installed tools such as Homebrew compile
  from source and need a toolchain. Do not remove it to reduce image size.
- **`docker-cli` and `docker.io` are both listed.** apt runs with
  `--no-install-recommends`, so `docker-cli` is not pulled in as a dependency of
  `docker.io` and must be named explicitly.
- **`apt-get upgrade -y` is intentional.** Packages are patched frequently, and the weekly
  rebuild is meant to pick those patches up. This trades build reproducibility for
  security.

### Container privileges

- Nested Docker is a hard requirement. The container does not use `privileged: true`;
  it uses `SYS_ADMIN`, `NET_ADMIN`, `/dev/fuse`, and seccomp/AppArmor/system paths
  unconfined, which hides host devices.
- The entrypoint remounts `/sys/fs/cgroup` read-write so nested containers can start.
- `cloud-user` has passwordless sudo and Docker group access by design.

### Port forwarding

- code-server's `/proxy/<port>/` and `/absproxy/` are intentionally enabled. They pass
  through the same Envoy JWT checks as the IDE. Do not block or reroute them.

### Access lifetime and signing keys

- Keep the two-hour absolute WebSocket lifetime and one-hour idle timeout. Tokens
  are verified on connection establishment; the cap bounds existing sessions.
- Keep synchronous JWKS refresh and fail closed after the one-hour key cache expires.
  Do not switch to asynchronous refresh that can retain stale keys during outages.
- Normal container health must work offline. The optional
  `container-healthcheck --access` probe checks online JWKS reachability separately; it does not prove user
  authorization. cloudflared waits for normal container health before starting.
- Envoy admin uses its own mode 600 Unix socket. The workspace user should not have
  direct access to it, even though passwordless sudo intentionally permits root access.

### Resource use and backups

- `manage start` caps workspace memory at 80% of the active Docker daemon host's
  reported total, including Docker Desktop/remote contexts. Leave CPU unlimited.
- cloudflared has a fixed 256 MiB memory cap. Do not invent a workspace PID cap
  without sizing it for developer processes and nested containers.
- Backups default to seven-day retention, scoped to the deployment's filename
  prefix. `BACKUP_RETENTION_DAYS=-1` explicitly disables pruning. S3 credentials
  need list/delete permissions when pruning is enabled.
- The backup sidecar intentionally uses the host Docker socket to stop/restart
  the workspace. Its `:ro` mount is not a Docker API permission boundary.
- Mutable release tags and weekly security rebuilds are intentional. Installers
  preserve existing digest pins; `manage upgrade` deliberately replaces them.

### Host tooling and cleanup

- Do not add daemon-wide `just stop` or `just cleanup` commands. Each command must
  clean up its own temporary files, containers, networks, and volumes; preserve
  deployment volumes, built images, and reusable build cache.
- `just install` installs GitHub CLI (`gh`). Bootstrap Python and deployment files
  require verified checksum attestations from the publishing repository/workflow
  before execution or replacement. Do not silently bypass verification.
- Bootstrap and `manage.py` embed the same checksum-pinned GitHub CLI downloader so
  neither needs to execute an unverified helper. Pins live in `build/build-versions.json`;
  `sync-pins` updates both copies and tests check that their implementations match.
  Installers use temporary binaries; Debian `just install` can install into `/usr/local/bin`.
- Restores reserve one named container per volume across the whole operation.
  Stop active helpers before staging cleanup, and retain staging/rollback after
  an interrupted replacement. A stale reservation after SIGKILL requires inspection.

<!-- TODO: Add further intentional decisions here, one heading per area. -->

## Out of scope for reviews

- Nested `--network host` / `127.0.0.1:8081` findings (see project memory).

<!-- TODO: Add other findings that reviewers should skip, with a one-line reason. -->

## Commands

<!-- TODO: Confirm and extend. Commands below come from the justfile. -->

- `just check`: shell syntax, shellcheck, unit tests, Compose configs.
- `just test`: check plus proxy, smoke, and backup integration tests.
- `just test-smoke`: full-container startup test; needs a Docker host that allows
  `SYS_ADMIN` and unconfined seccomp/AppArmor/system paths.

## Conventions

<!-- TODO: Code style, comment density, naming, commit message rules, PR expectations. -->

## Security notes

<!-- TODO: Threat model, trust boundaries, secrets handling, what Access users can do. -->

## Known issues and follow-ups

<!-- TODO: Link to CODE_REVIEW.md items or track new ones here. -->

See [CODE_REVIEW.md](CODE_REVIEW.md) for the remaining review findings.
