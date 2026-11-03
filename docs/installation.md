[← Project overview](../README.md)

## Install

### Requirements

Supported hosts are Debian Linux and macOS with a working Docker installation,
Docker Compose, and Python 3. The bootstrap also needs curl. The installer
checks for Docker Compose but never installs host packages; if Compose is
missing, install `docker-compose-plugin` (Docker CE), `docker-compose` (Debian's
`docker.io`), or use Docker Desktop or Homebrew on macOS. Restoring encrypted
backups additionally needs `gpg` on the host; see [restore](restore.md).

No GitHub account, token, or GitHub CLI is needed.

### Run the installer

```sh
curl -fsSL https://raw.githubusercontent.com/Weeks-Consulting/code-server-aio/refs/heads/main/build/tools/install.sh | sh
```

From a checkout, run `./manage install` instead. Both install into
`~/code-server` by default; run as root (for example `curl ... | sudo sh`), the
default is `/opt/code-server`. Options:

| Option | Purpose |
| --- | --- |
| `--directory PATH` | Install somewhere else. The directory, or its parent, must be writable. |
| `--version vX.Y.Z[-N]` | Install a specific release instead of the latest stable one. |
| `--repository OWNER/REPO` | Install from a fork. |
| `--dry-run` | Show what would change without downloading or changing anything. |

To install a specific release as root into `/opt/code-server`, use its tagged
bootstrap and select the same version:

```sh
curl -fsSL https://raw.githubusercontent.com/Weeks-Consulting/code-server-aio/refs/tags/v4.144.0-1/build/tools/install.sh \
  | sh -s -- --version v4.144.0-1 --directory /opt/code-server
```

The bootstrap resolves one release before downloading `manage.py` and
`SHA256SUMS`, then passes that version to the verified installer. Its helper,
checksums, deployment bundle, and image therefore use the same release, including
when the latest release changes during installation. `--repository` also applies
to bootstrap downloads. Without `--version`, the bootstrap resolves the latest
stable release once and selects its versioned image.

Pass options to the bootstrap after `sh -s --`, for example
`... | sh -s -- --directory /srv/code-server`.

Run `manage` as the same user that installed it; if you installed with `sudo`,
run `manage` with `sudo` too. On macOS, keep the deployment and backup folders
somewhere shared with Docker, such as your home directory.

Then configure `.env` as described in [configuration](configuration.md#setup) and
start with `~/code-server/manage --pull`. The installer never starts services.

### What the installer does

1. Downloads the release's `code-server-aio.tar.gz` bundle and `SHA256SUMS` over
   HTTPS and checks the bundle against `SHA256SUMS`. The bootstrap checks
   `manage.py` the same way before running it. Any missing file or checksum
   mismatch stops installation before the deployment changes.
2. Installs the Compose files, `manage`, `manage.py`, `restore-home.sh`, and
   `backup-key.py`, saving previous copies as `.bak` files.
3. Creates `.env` (mode 600) from the release template, or keeps the existing
   values and comments and appends settings the new release added.
4. Sets `CODE_SERVER_IMAGE` to the selected release tag (`:latest` when invoking
   `manage install` directly without `--version`), unless it contains a digest
   pin, which is kept with a warning.
5. Generates `BACKUP_GPG_PASSPHRASE` if it is missing or empty. Save it
   separately; archives cannot be recovered without it.

Releases are installable once their images have been tested and published; until
then the installer reports that the release is still publishing.

Checksums detect corrupted or altered downloads; GitHub (over HTTPS) and the
release publisher remain the trust anchor. To check provenance yourself, see
[verification](verification.md).

### Refresh or upgrade

Rerun the installer (or `manage install --directory PATH`) to refresh the files
for the selected release while keeping any digest pin. To move to the latest
release, use `manage upgrade`; see [deployment](deployment.md#upgrade-to-the-latest-release).
Review new `.env` settings, then run `manage --pull` with your usual options.

Refreshes serialize on `.env.lock`, and replacements are staged so a failed
refresh restores the previous deployment, including the encryption key. If the
rollback itself fails, the installer names the private `.install-*` directory
holding the recovery files. A process or host crash can still leave mixed
release files: inspect the deployment and its `.bak` files before restarting.
