[← Project overview](../README.md)

## Build

Use a source checkout with a working Docker installation including Compose and
Buildx. Run all Just commands from the checkout, including from zsh.

### Just commands

All available recipes are listed here:

```sh
just                 # List commands
just install         # Install host build and test dependencies
just version         # Print shipped dependency versions (offline)
just build-version   # Print build/test tooling versions (offline)
just build-outdated  # Check build/test tooling for updates
just build-upgrade   # Review and update build/test tooling
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
Debian. It does not install or launch a deployment; use [Install](installation.md#install) for
published releases and [Deploy](deployment.md#deploy) to start services.

Builds read the code-server version from the `packages.code-server.version` entry in `build/versions.json`. Building the
local test image needs no `.env` or Cloudflare credentials.

Override the local image tag with `just image=my-image:test test`. Test recipes
use isolated containers and temporary storage. CI runs the same scripts from
`build/tests/`, which can also be run directly, for example
`./build/tests/smoke.sh code-server-cloudflare:test`.

### Test details

Host unit tests use `python3 -m unittest discover -s build/tests -p "test_*.py"`.
New host tests follow `test_*.py`; container and topology programs use
`integration_*.py` and run through `just test-proxy`.

`just test-proxy` checks the real Envoy binary against disposable HTTPS signing keys
and a Unix-socket fixture: JWT claims, duplicate headers, allowlists, TLS validation,
key rotation and outages, error pages, and WebSocket lifetime limits. It also starts
the production Compose topology with real code-server and a temporary JWKS service,
then checks that a client on the default network cannot reach the backend socket or admin
listener, container restarts, and rejection of requests while either service is unavailable.
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
The wrapper disables act's automatic `.env` loading so deployment settings do not
override the workflow's test configuration.

The smoke test needs privileged Docker. On macOS it uses named volumes to preserve Linux UID/GID ownership;
on Linux it also exercises an empty home bind mount. Temporary bind-mounted test
files are created in ignored `.test-tmp.*` directories under the checkout, which
must be shared with Docker. The restore test uses temporary volumes and encrypted
local archives, without S3.

Host scripts are checked as POSIX `sh` and tested on Debian and macOS in CI.
Bash is still used to check the container's Linux runtime scripts and CI release
logic; the version bundled with either host is sufficient.

### Dependency updates

`build/versions.json` holds shipped image and deployment dependencies: code-server, Envoy, the Debian slim base, cloudflared, and the backup service image. `just version`, `just outdated`, and `just upgrade` inspect, check, and update this group.

`build/build-versions.json` holds build/test tooling: act, GitHub Actions, the act runner image, and RustFS for backup tests. Use `just build-version`, `just build-outdated`, and `just build-upgrade` for this group. Debian CI and the restore helper share the shipped Debian slim base pin.

`just upgrade` requires Python 3 and Internet access. It prints current and latest upstream pins and asks `[y/N]` before changing files. Each upgrade command updates only its group, including package checksums for every supported architecture and image digests on their existing tag tracks. The build tooling command also updates GitHub Actions commit pins and version comments.
Files are
staged before replacement; write failures attempt to restore previous contents. A
process or host crash during replacement can still require recovery using Git. GitHub asset SHA-256 metadata is used when available; otherwise assets are downloaded and hashed. Set `GH_TOKEN` or `GITHUB_TOKEN` if needed for GitHub API rate limits. Lookup failures and a declined or unanswered prompt leave files unchanged. Review `git diff` and run `just test` afterward; rebuilding/redeploying is a separate step.

GitHub Actions remain pinned to full 40-character commit SHAs in `uses:`. During `just build-upgrade`, the latest stable release of each Action is resolved to its commit SHA; approval updates that hash and the adjacent release-version comment in every workflow occurrence. The version comment is informational; the commit SHA selects the code that runs. Actions are included in the build tooling confirmation prompt.

`just version`, `just outdated`, and `just upgrade` hide commit SHAs and image digests by default. `just outdated` checks the same upstream sources as `just upgrade`, lists only outdated dependencies with their current and latest versions, and reports when all pins are current. It never prompts or changes files, and skips package downloads needed only for checksum updates. It requires Python 3 and Internet access. A completed check exits successfully whether or not updates are available; lookup failures exit with an error.

The `build-` commands have the same output, confirmation, and offline behavior as their counterparts. Add `--hashes` to any dependency command to include commit SHAs and image digests: `just version --hashes`, `just outdated --hashes`, or `just upgrade --hashes`. Hash changes are still detected and reported as available updates when hashes are hidden.

Code-server packages are verified against `packages.code-server.sha256` in `build/versions.json`. When updating the `packages.code-server.version` entry in `build/versions.json`, update both architecture checksums from the upstream release metadata.

Actions and service images are pinned to immutable revisions; update those pins deliberately and run the integration suite. The temporary act download is pinned and checked against `packages.act.sha256` in `build/build-versions.json`.

