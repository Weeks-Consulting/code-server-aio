[← Project overview](../README.md)

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

GitHub authentication is required to verify release attestations, including for
public releases. Run `gh auth login --hostname github.com` as the installing user,
or export `GH_TOKEN` (a classic personal access token with no scopes is sufficient
for public releases). `GITHUB_TOKEN` is also accepted. The installer checks for
credentials before installing packages or downloading files; invalid or expired
credentials will still fail verification. Dry runs do not require authentication.

Installation and management run through `manage`, a thin shell wrapper around
`manage.py` using Python's standard library. From a checkout, install with:

```sh
./manage install --directory "$HOME/code-server"
```

For hosts without a checkout, install curl and Python 3 first. If the installed
GitHub CLI cannot run `gh attestation verify`, the bootstrap and Python installer
download an official, version-pinned GitHub CLI binary and verify its SHA256
before running it. The binary is temporary and supports Linux/macOS on amd64/arm64;
no apt repository is added. `just install` installs a compatible CLI in
`/usr/local/bin` on Debian when needed, or through Homebrew on macOS.
The bootstrap verifies the release checksum attestation and the downloaded
`manage.py` checksum before executing it. The Python installer also verifies the
checksum attestation before replacing deployment files. When using sudo, export
`GH_TOKEN` and preserve it as shown below; your normal user’s `gh` login is
generally unavailable to root. Verification failures stop installation.

```sh
curl -fsSL https://raw.githubusercontent.com/Weeks-Consulting/code-server-aio/refs/heads/main/build/tools/install.sh | sudo --preserve-env=GH_TOKEN sh -s --
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
files before replacing any files. The current installer requires releases with all deployment files (including
`compose.backup-common.yaml`), Python management, backup helpers, and `SHA256SUMS`.
To deploy an earlier release, use that release's own installer. Missing files or
checksums abort installation before deployment files are changed.
Attestations must identify the selected repository and its container publishing
workflow. GitHub and the release publisher remain the trust anchor. Rerunning the installer refreshes the
Compose files, saving their previous contents as `.bak` files. Existing `.env`
values and comments are preserved except for `CODE_SERVER_IMAGE`, which is set to the selected tag unless it contains a digest pin; missing settings are appended with notes from
the new release. `.env` is readable only by its owner. If you run the installer
with `sudo`, you must also run `manage` with `sudo`. Otherwise, run `manage`
as the same user that installed the deployment. This applies to all launcher
commands, including updates, backups, and restores, and to direct Docker Compose
commands that read this `.env`.
The installer does not start
or restart services; start them with `manage --pull` after configuring `.env`.
All services use `restart: unless-stopped`: once deployed, Docker restarts them
when they exit or the Docker daemon restarts, unless you explicitly stop them.

When upgrading a deployment installed before `compose.backup-common.yaml` was
introduced, use the bootstrap above or `manage install` from an updated checkout.
The older installed launcher does not download the common backup file.

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


Installer refreshes serialize on `.env.lock`. Complete replacements, including
the encryption key, are staged before installation; failed replacements restore
the previous deployment. If rollback itself fails, the installer reports the
private `.install-*` directory containing recovery files. A process or host crash
can still leave mixed release files: inspect the deployment and its `.bak` files
before restarting.
