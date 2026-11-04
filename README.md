# All-in-One Development Environment with Cloudflare Access

Deploy a complete, browser-based development environment with code-server and Docker-in-Docker using Docker Compose. Run each instance on its own dedicated VM, which is the isolation boundary, and access it remotely through Cloudflare Tunnel, with authentication enforced through Cloudflare Access policies and local token validation.

## AI disclosure

AI tools assisted with the development, documentation, and review of this project.
The entire project has also been reviewed and tested by a real person.

## Quick start

Requires Docker with Compose, Python 3, and curl; no GitHub account or GitHub CLI
is needed. See the [installation guide](docs/installation.md) for options and
release selection.

```sh
curl -fsSL https://raw.githubusercontent.com/Weeks-Consulting/code-server-aio/refs/heads/main/build/tools/install.sh | sh
```

Edit `~/code-server/.env` and set `CF_TUNNEL_TOKEN`,
`CF_ACCESS_TEAM_DOMAIN` (your `team.cloudflareaccess.com` hostname), and
`CF_ACCESS_AUD` (the application's 64-character audience tag).
Configure the tunnel origin as `http://code-server:8080` and protect its public
hostname with a Cloudflare Access application and a policy for trusted users.
Then start the deployment:

```sh
~/code-server/manage configure --pull --start
```

Anyone granted access effectively has root access to the Docker host. Use a
host dedicated to this workspace and restrict Access to trusted users.
See [configuration and Access protection](docs/configuration.md) for details.

Open `https://<your-hostname>/` after signing in.

Save projects under `/home/cloud-user`. Named volumes preserve `/home` and
nested Docker data across container recreation. Stopping the deployment keeps
these volumes. See [operations](docs/operations.md) for logs and customization.

## Commands

Run `manage` from the installation directory, or use its full path. If installed
with `sudo`, run it with `sudo` too. `configure` reads `.env` and saves the resolved
configuration, including backup mode and project name, for later commands.

| Command | Purpose |
| --- | --- |
| `./manage configure --pull --start` | Configure containers, pull images, and start. |
| `./manage configure --backup local --start` | Configure and start with local backups. |
| `./manage configure --backup s3 --start` | Configure and start with S3 or R2 backups. |
| `./manage start` | Start existing configured containers (default command). |
| `./manage stop` | Stop containers; keep containers, networks, and volumes. |
| `./manage logs [-f]` | Print logs; add `-f` to follow. |
| `./manage upgrade` | Install latest release files and image setting; apply with `configure --pull --start`. |
| `./manage install` | Install or refresh deployment files. |
| `./manage backup` | Run a backup inside the configured, running backup service. |
| `./manage backups` | List configured local archives. |
| `./manage restore --archive FILE --volume NAME` | Restore an archive into an existing home volume. |
| `./manage --help` | Show every command and its applicable options. |

See the [command option reference](docs/deployment.md#command-option-reference) for
all options. Run `configure --start` to apply changed `.env` or Compose settings;
`start` resumes the existing containers. Later configuration runs retain your
backup mode and project name; use `configure --backup none` to disable backups.

Configure the selected backup destination in `.env` before enabling it. Backups
pause the application while archiving `/home`; nested Docker data is excluded.
Save `BACKUP_GPG_PASSPHRASE` separately: archives cannot be recovered without it.
See [backups](docs/backups.md) and [restore](docs/restore.md).

For development, run `just` to list recipes, `just check` for host checks,
`just build` to build the test image, `just run` to build and start the local
deployment using the checkout's `.env`, and `just test` for the full suite.
See the [development guide](docs/development.md) for requirements and dependency updates.

## Guides

| Guide | Contents |
| --- | --- |
| [Installation](docs/installation.md) | Host setup, installer options, release selection, and refresh behavior. |
| [Deployment](docs/deployment.md) | Launch options, upgrades, shutdown, and local builds. |
| [Configuration](docs/configuration.md) | Environment reference and Cloudflare Access protection. |
| [Operations](docs/operations.md) | Service lifecycle, persistence, logs, and customization. |
| [Backups](docs/backups.md) | Encryption, local folders, S3, scheduling, and retention. |
| [Restore](docs/restore.md) | Recovery and rollback. |
| [Development](docs/development.md) | Builds, tests, local Actions runs, and dependency pins. |
| [Publishing](docs/publishing.md) | CI, release tags, and weekly rebuilds. |
| [Verification](docs/verification.md) | Image and checksum provenance, and digest pins. |
