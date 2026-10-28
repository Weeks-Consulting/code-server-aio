[← Project overview](../README.md)

## Operation and customization

```sh
docker compose logs -f
docker compose exec --user cloud-user code-server bash
docker compose exec code-server supervisorctl status
docker compose down
```

The container healthcheck expects HTTP 403 from an unauthenticated request to the
real Envoy listener every 30 seconds. Only the exact `GET /` probe with the
`code-server-container-healthcheck` User-Agent, no JWT assertion header, and a
physical TCP peer of `127.0.0.1` is quiet. Suppression is marked inside the
missing-token rejection code and requires a 403 response. Forwarding headers do
not establish local origin. Other denials and unexpected probe responses remain
logged. The probe verifies anonymous access is denied without requiring online
key retrieval; JWT signature validation is also covered by integration tests.

Envoy writes JSON access logs containing timestamp, method, actual request path
without query parameters, host, protocol, status, response details, denial reason
when available, response flags, direct peer address, duration, User-Agent, and
request ID. JWT headers and cookies are not logged. The peer is the immediate TCP
connection (normally cloudflared), not an inferred end-user IP.

code-server runs as `cloud-user` (UID/GID 10000), with passwordless sudo and Docker
group access. Supervisor manages the services; the container runs with
`SYS_ADMIN`, `NET_ADMIN`, `/dev/fuse` and relaxed seccomp/AppArmor/system paths for
Docker-in-Docker, instead of `--privileged`; host devices are not exposed. The entrypoint
remounts `/sys/fs/cgroup` read-write for nested containers. Startup repairs only the home directory owner and preserves ownership of its contents.
If any supervised service exhausts its startup retries, Supervisor shuts down so
Docker's restart policy can restart the container. Missing or invalid Access
configuration prevents Envoy from starting until corrected.

Save projects under `/home/cloud-user`; `/workspace` is not provided.

Named volumes persist all of `/home` (user files, settings, extensions, and shared
installations such as `/home/linuxbrew`) and
`/var/lib/docker`. `docker compose down` keeps them; `down -v` deletes them.

You can install [Homebrew](https://docs.brew.sh/Homebrew-on-Linux) yourself after startup.
Its installation and shell configuration persist in the mounted `/home` volume.

The entrypoint prepares the private socket directory and mounted home directory
before starting Supervisor. If preparation fails, the container exits and Docker's
restart policy retries it. Supervisor starts code-server as `cloud-user` and Envoy
as a separate user in the same container. Restarting an individual service does
not repeat workspace initialization.

Envoy configuration is rendered from `build/runtime/envoy.json.template` at startup;
changing Access settings with `manage start` recreates the application container.
Code-server starts with `--socket` and removes a stale socket before binding it.

If reusing the previous home-volume layout, move its existing contents into a
`cloud-user/` subdirectory before restarting with this mount. Startup creates the home directory as needed
without changing ownership of its contents or other home directories.

Add apt packages in `build/Dockerfile`, one-time startup commands in `build/runtime/entrypoint.sh`, and
foreground services in `build/runtime/supervisord.conf`. Every supervised service is
critical: the health check requires it to be running, and repeated startup failure
restarts the container.

Files shipped in the application image live in `build/runtime/`, including startup
scripts and Supervisor configuration. Envoy configuration and error pages are
embedded in `build/runtime/envoy.json.template`. Build-only version and checksum
files stay beside `build/Dockerfile`. Host installation and restore helpers live
in `build/tools/`; tests stay in `build/tests/`. Compose files stay at the project root.


For a separate online JWKS reachability probe, run
`docker compose exec -T code-server /usr/local/bin/container-healthcheck --access`.
Normal Docker health deliberately works offline and does not fetch keys. The
optional probe checks HTTPS connectivity and HTTP success, not a user's authorization.
