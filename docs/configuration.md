[← Project overview](../README.md)

## Setup

Anyone granted Cloudflare Access to this development environment (passwordless
sudo, Docker group, `SYS_ADMIN` with relaxed seccomp) should be treated as having
root access to the Docker host. Restrict the Access policy to
specific trusted users. A Service Auth token also grants root access to the VM;
protect it as a root credential. For defense in depth, set the optional origin
allowlists below in addition to restricting the Access policy.

1. Edit the installer's `.env` (or copy `.env.example` when using a checkout) and set:
   - `CODE_SERVER_IMAGE`: the published GHCR image (selected by the installer; defaults to the project image in a checkout).
   - `CF_TUNNEL_TOKEN`: your remotely managed tunnel token.
   - `CF_ACCESS_TEAM_DOMAIN`: your `team.cloudflareaccess.com` hostname, without `https://`.
   - `CF_ACCESS_AUD`: the 64-character Application Audience tag copied exactly from your Access application, including letter casing.
2. Configure the tunnel origin as `http://code-server:8080` and protect the hostname
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

Multiline values are unsupported in `.env`. Keep each value on one physical line;
installation, image updates, and backup key initialization reject multiline or
unterminated quoted values before changing settings.

Edit `.env` beside the Compose files. Values such as `replace-with-...` and
`your-team` in `.env.example` are placeholders to replace, not working defaults.
Compose uses the defaults below when an optional value is unset or empty.
Variables exported in your shell take precedence over `.env`.

After changing runtime settings, rerun `docker compose up -d` with your usual
Compose file options to recreate affected containers. `docker compose restart`
does not apply environment changes. Changing a build version also requires
`--build`. Backups require `compose.backup-common.yaml` followed by either
`compose.backup.yaml` (S3) or `compose.backup-local.yaml` (local folder).
Use one destination at a time, or let `manage --backup local|s3` select the files.
Setting backup variables alone does not enable them.

### Application and local builds

| Property | Required / default | Description |
| --- | --- | --- |
| `CODE_SERVER_IMAGE` | Optional; `ghcr.io/weeks-consulting/code-server-aio:latest`. | Full registry image reference to pull, such as `ghcr.io/weeks-consulting/code-server-aio:4.141.0` or `:4.141.0-1`. The installer sets this to `:latest` by default or the requested version on every installation, preserving existing digest pins. `latest` follows stable releases. Ignored when `compose.build.yaml` selects the local image. |
| `CF_TUNNEL_TOKEN` | Required; no default. | Tunnel token for your remotely managed Cloudflare Tunnel, used by the cloudflared sidecar. Configure the tunnel's origin service as `http://code-server:8080`. This is the tunnel token, not a Cloudflare API token or an Access service token. |
| `CF_ACCESS_TEAM_DOMAIN` | Required for access; no default. | Cloudflare Access team hostname, such as `my-team.cloudflareaccess.com`. Supply only the hostname, without `https://`, a path, or a trailing slash. Envoy fetches signing keys from this team and checks the JWT issuer. Missing or invalid configuration prevents Envoy from starting. |
| `CF_ACCESS_AUD` | Required for access; no default. | The Access application's 64-character hexadecimal Application Audience (AUD) tag. Audience matching is case-sensitive: copy the value exactly as Cloudflare provides it, without changing letter casing. Use the application protecting this service, not the tunnel ID or account ID. Tokens with a different audience receive HTTP 403; missing or malformed configuration prevents Envoy from starting. Find it under **Zero Trust → Access controls → Applications → Configure → Additional settings**. |
| `CF_ACCESS_ALLOWED_EMAILS` | Optional; empty. | Comma-separated exact, case-sensitive email claims, for example `alice@example.com,bob@example.com`. |
| `CF_ACCESS_ALLOWED_SERVICE_TOKENS` | Optional; empty. | Comma-separated exact `common_name` claims from Access service-token JWTs. If either allowlist is set, the signed token must match an email OR a service identity; missing claims and spoofed headers do not match. Invalid list syntax stops startup. Both empty delegates identity authorization to the Access policy. |

### Optional backups

The backup container controls the host through the Docker API. Mounting its
Docker socket read-only does not restrict API operations.

These properties apply when either backup overlay is included. S3 settings apply
only to `compose.backup.yaml`; local backups need no S3 credentials. Backups stop
the code-server container while archiving all of `/home`, then restart it. They
exclude nested Docker's `/var/lib/docker`. For S3, create the destination bucket
first and allow listing and deleting matching objects for automatic pruning. Local and S3
archives older than seven days are pruned by default, restricted to the deployment's
archive filename prefix.

| Property | Required / default | Description |
| --- | --- | --- |
| `BACKUP_RETENTION_DAYS` | Optional; `7`. | Prune matching local and S3 archives older than this many days. Set `-1` to disable pruning. |
| `BACKUP_NOTIFICATION_URLS` | Optional; empty. | Comma-separated Shoutrrr notification URLs; empty disables notifications. |
| `BACKUP_NOTIFICATION_LEVEL` | Optional; `error`. | Use `error` for failures or `info` for all completed runs. |
| `BACKUP_TIMEZONE` | Optional; `America/Chicago`. | Timezone used by the backup schedule. Use an IANA timezone such as `America/Chicago` or `Etc/UTC`. The schedule follows that timezone, including its daylight-saving changes. |
| `BACKUP_CRON_EXPRESSION` | Optional; `0 3 * * *`. | Five-field cron schedule: minute, hour, day of month, month, day of week. The default runs daily at 03:00 in `BACKUP_TIMEZONE`. Quote the value in `.env`, for example `BACKUP_CRON_EXPRESSION="0 3 * * *"`. |
| `BACKUP_LOCAL_PATH` | Optional; `./backups`. Local overlay only. | Host folder receiving `.tar.gz.gpg` archives. Relative paths resolve beside the Compose files, so the installer's default is `/opt/code-server/backups`. Use an absolute path such as `/mnt/backups/code-server` to select another location. Compose creates the folder if it does not exist and mounts it at `/archive` in the backup container. S3 settings are not passed to the local backup container. |
| `BACKUP_AWS_S3_BUCKET_NAME` | Required for S3 backups; no default. | Name of an existing S3 or S3-compatible bucket. Supply the bucket name alone, without `s3://`, an endpoint, or an object prefix. |
| `BACKUP_ID` | Optional; Compose project name. | Identifies this deployment in archive names and in the default S3 prefix. Set a unique value, such as `devbox-home`, when multiple hosts share a bucket. Archive names are `<BACKUP_ID>-home-<timestamp>.tar.gz.gpg`; an empty value uses the Compose project name. It does not change the container stop label, which remains scoped to the Compose project. |
| `BACKUP_AWS_S3_PATH` | Optional; `<BACKUP_ID or Compose project name>/home`. | Object-key prefix inside the bucket, such as `development/devbox/home`. An explicit value overrides the prefix derived from `BACKUP_ID`; an empty value selects the default. This controls where archives are stored, not the bucket addressing style. |
| `BACKUP_AWS_ENDPOINT` | Optional; `s3.amazonaws.com`. | S3 service hostname without `https://` or a bucket path. For Cloudflare R2, use `<account-id>.r2.cloudflarestorage.com`. Use the endpoint supplied by your storage provider. |
| `BACKUP_AWS_S3_BUCKET_LOOKUP` | Optional; `auto`. | Bucket addressing style: `auto` selects based on the endpoint; `path` addresses the bucket as `endpoint/bucket`; `dns` uses `bucket.endpoint`. R2 normally works with `auto`; use `path` when explicitly required by your endpoint. Independent of `BACKUP_AWS_S3_PATH`. |
| `BACKUP_AWS_ACCESS_KEY_ID` | Required for S3 backups; no default. | S3 access key ID with permission to upload backup objects to the selected bucket and prefix. For R2, use an R2 S3 access key ID, not a general Cloudflare API token. Pair it with `BACKUP_AWS_SECRET_ACCESS_KEY`. |
| `BACKUP_AWS_SECRET_ACCESS_KEY` | Required for S3 backups; no default. | Secret associated with `BACKUP_AWS_ACCESS_KEY_ID`. Use the S3-compatible credentials issued by your storage provider. |

Compose also accepts `COMPOSE_PROJECT_NAME`, although it is not listed in
`.env.example`. By default, the project name comes from the deployment directory
(`code-server` for `/opt/code-server`); `docker compose -p NAME` overrides it.
This name scopes Docker volumes, backup stop labels, and the default backup ID.
Changing it selects a different set of named volumes, so keep it consistent for
an existing deployment.

## Access protection

Traffic flows through cloudflared → Envoy (`:8080`) → code-server over
`/run/code-server/backend.sock`. Configure the remotely managed tunnel origin as
`http://code-server:8080`. Code-server has **no TCP listener** and no published ports.
The backend socket stays inside the workspace container; cloudflared and backup
containers have no socket mount. Envoy listens on port 8080, while its admin
interface uses `/run/envoy/admin.sock`, a mode `600` Unix socket owned by Envoy. Services share the project-scoped
Compose default network and reach the protected listener; the backend has no TCP listener.

The application build downloads a static Envoy executable pinned by
`packages.envoy.version` in `build/versions.json` and architecture-specific checksums in `packages.envoy.sha256` in `build/versions.json`.
Supervisor runs it as a dedicated `envoy` user with group 10000 for backend socket
access. Envoy starts alongside workspace initialization. The container healthcheck
checks Supervisor, backend readiness, Envoy readiness, and rejection of unauthenticated
requests. Exhausted startup retries trigger a container restart.

`build/runtime/start-envoy.py` renders `build/runtime/envoy.json.template` into
`/run/envoy/envoy.json` at startup using JSON-aware string substitution, then
executes Envoy. No additional templating dependency is required. All four Access
variables are passed to the workspace container. `just upgrade` updates the binary
version and both architecture checksums alongside the other dependency pins.

**Existing deployments:** change the remotely managed tunnel origin from
`http://envoy:8080` to `http://code-server:8080` when deploying this version.
Rebuild or pull the updated workspace image, and use `docker compose up -d --remove-orphans` with your usual Compose options to remove the old Envoy sidecar.

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
- **503:** A valid authenticated request reaches Envoy while code-server is starting or the backend is unavailable.
- **Connection unavailable:** Envoy is stopped or its environment configuration is missing/invalid. Check `docker compose logs code-server`.

The 403 and 503 pages include troubleshooting steps; the 503 page retries every five
seconds. Customize `local_reply_config` in `build/runtime/envoy.json.template`
to change the error pages.

WebSocket tokens are checked when the connection opens. Idle streams close after
one hour, and all streams have an absolute two-hour lifetime, independent of key
refresh success. Open sockets are not revalidated when their JWT expires. To revoke
access immediately, remove the user or service token from the Access policy, revoke
its Access sessions, then run `docker compose restart code-server` with the deployment's
usual Compose options to disconnect existing sockets. Apply origin allowlist changes
with `manage start`, which recreates the workspace container with its updated environment.

The entrypoint completes workspace setup before starting Supervisor and its services.
While code-server starts, authenticated visitors can see the startup page. The
container health check verifies running supervised services, nested Docker, the
backend Unix socket, Envoy listener readiness, and rejection of unauthenticated
requests. It does not prove that a JWKS fetch will succeed or that a particular
user can authenticate. Initialization failures exit the container so Docker's
restart policy can retry.

## Memory and logs

`manage start` asks the active Docker daemon for its host memory total and limits
the workspace to 80% of that value. With Docker Desktop or a remote Docker context,
this is the daemon's VM or host, not the client machine. CPU use is unlimited.
cloudflared has a fixed 256 MiB memory limit. Direct Compose users can export
`CODE_SERVER_MEMORY_LIMIT` as a byte count or Docker memory quantity; its default
is unlimited without `manage`. All deployment services use JSON logs rotated at
10 MiB per file, keeping three files per container.
