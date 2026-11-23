[← Project overview](../README.md)

## Operation and customization

```sh
./manage logs -f
project=my-workspace  # the saved Compose project name (see below)
docker compose -p "$project" exec --user cloud-user code-server bash
docker compose -p "$project" exec code-server systemctl status code-server envoy dockerd
./manage stop
```

Run these from the deployment directory. Direct `docker compose` commands do not read the
project name saved by `manage configure`; without `-p`, Compose uses `COMPOSE_PROJECT_NAME`
or the directory name and can reach a different deployment on the same daemon. Print the saved
name with `python3 -c 'import json; print(json.load(open(".manage.compose.json"))["name"])'`
(with `sudo` if you installed with `sudo`).

`./manage logs` prints logs from all services and exits. Add `-f` (or `--follow`)
to keep streaming new output; press Ctrl+C to stop following. Logs use the backup mode and project name saved by `configure`.

The container healthcheck expects HTTP 403 from an unauthenticated request to the
real Envoy listener every 30 seconds. Only the exact `GET /` probe with the
`code-server-container-healthcheck` User-Agent, no JWT assertion header, and a
physical TCP peer of `127.0.0.1` is quiet. Suppression is marked inside the
missing-token rejection code and requires a 403 response. Forwarding headers do
not establish local origin. Other denials and unexpected probe responses remain
logged. The probe verifies anonymous access is denied without requiring online
key retrieval; JWT signature validation is also covered by integration tests.

With the Compose TTY, Envoy writes raw JSON access records to the Docker log
stream. They include timestamp, method, actual request path without query
parameters, host, protocol, status, response details, denial reason
when available, response flags, direct peer address, duration, User-Agent, and
request ID. JWT headers and cookies are not logged. The peer is the immediate TCP
connection (normally cloudflared), not an inferred end-user IP. Workspaces
started without a TTY send Envoy's access records to the journal.

code-server runs as `cloud-user` (UID/GID 10000), with passwordless sudo and Docker
group access. systemd runs as PID 1 in the workspace's private cgroup v2
namespace. The container uses `SYS_ADMIN`, `NET_ADMIN` and relaxed
seccomp/AppArmor/system paths for Docker-in-Docker. The entrypoint uses a bind
remount to make `/sys/fs/cgroup` writable inside the workspace. Startup exits
with an error if the remount fails.

The nested Docker daemon, code-server, and the release update checker inherit
`HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` from the workspace container's
environment, including their lowercase forms.

Docker's restart policy can restart the workspace when dockerd, Envoy, or
code-server exceeds its systemd start limit. It also restarts the workspace if
the workspace user's runtime directory fails to initialize or the user manager
exhausts its retries. If the user D-Bus socket fails, systemd restarts the user
manager and starts its enabled services again. Missing or invalid Access
configuration prevents Envoy from starting until corrected.

Infrastructure diagnostics appear in `manage logs`. To read Envoy's
diagnostics by unit, run
`docker compose -p "$project" exec code-server journalctl -b -u envoy -o cat`.
The persistent journal has a 32 MiB budget so `journalctl --user` is available.
The volatile journal used during early boot has a 16 MiB budget.
Docker rotates its console logs independently.

When moving an existing deployment to systemd, use `manage upgrade` to update
the image and Compose files together. The workspace is recreated with the
existing home and Docker volumes. An image-only refresh from an older saved
configuration continues in Supervisor compatibility mode until the deployment
is recreated with the updated Compose configuration. Homebrew services become
available after that migration.

Save projects under `/home/cloud-user`; `/workspace` is not provided.

Named volumes persist all of `/home` (user files, settings, extensions, and shared
installations such as `/home/linuxbrew`) and
`/var/lib/docker`. `manage stop` keeps them along with the containers. Only an explicit
`docker compose -p "$project" down -v` deletes them; a plain `down` also removes the
containers, so `manage start` then fails until you run `manage configure --start` again.

Install [Homebrew](https://docs.brew.sh/Homebrew-on-Linux) from the code-server
terminal and follow its shell setup instructions. For a formula that defines a
Linux service, run `brew services start FORMULA` as `cloud-user`. Use
`brew services list` to check its status and `brew services stop FORMULA` to
stop and disable it. Enabled services start at container boot and keep running
when the browser closes. The Homebrew installation, shell configuration, and
user units under `~/.config/systemd/user` persist in the mounted `/home` volume.

The IDE terminal inherits the user bus settings. For a shell opened directly
with Docker Compose, pass the runtime directory explicitly.

```sh
docker compose -p "$project" exec --user cloud-user code-server env XDG_RUNTIME_DIR=/run/user/10000 bash -l
```

The entrypoint prepares the mounted home before starting systemd. At boot,
systemd creates the private socket directories and runs code-server as
`cloud-user` and Envoy as a separate user.

Envoy configuration is rendered from `build/runtime/envoy.json.template` at startup;
changing Access settings with `manage configure --start` recreates the application container.
Code-server starts with `--socket` and removes a stale socket before binding it.

If reusing the previous home-volume layout, move its existing contents into a
`cloud-user/` subdirectory before restarting with this mount. Startup creates the home directory as needed
without changing ownership of its contents or other home directories.

Add apt packages in `build/Dockerfile`, pre-init setup in `build/runtime/entrypoint.sh`,
and system service units in `build/runtime/systemd/`. The health check requires
code-server, Envoy, nested Docker, and the workspace user manager and bus.

Files shipped in the application image live in `build/runtime/`, including startup
scripts and systemd units. Envoy configuration is
in `build/runtime/envoy.json.template`; its Lua filters and error pages are separate
files in `build/runtime/envoy/lua/` and `build/runtime/envoy/pages/`. Build-only version and checksum
files stay beside `build/Dockerfile`. Host installation and restore helpers live
in `build/tools/`; tests stay in `build/tests/`. Compose files stay at the project root.


For a separate online JWKS reachability probe, run
`docker compose -p "$project" exec -T code-server /usr/local/bin/container-healthcheck --access`.
Normal Docker health deliberately works offline and does not fetch keys. The
optional probe checks HTTPS connectivity and HTTP success, not a user's authorization.
