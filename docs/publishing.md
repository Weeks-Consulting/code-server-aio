[← Project overview](../README.md)

## Image publishing

GitHub Actions builds and tests native amd64 and arm64 images on pull requests,
pushes to `main`, version tag pushes, manual runs, and weekly scheduled builds.
Version tag pushes and weekly scheduled builds upload images;
regular commits and manual runs run tests only by default. To publish an urgent
rebuild, use **Actions → Build and publish container → Run workflow** and enable
**Rebuild and publish the latest stable release**. This uses the latest stable
release source, bypasses the runtime stage's build cache, and applies the same tests and latest-release
checks as the weekly rebuild. Every Monday at 08:17 UTC
(03:17 in Chicago during daylight saving time, 02:17 during standard time), the
scheduled build looks up the latest stable GitHub release tag and starts a manual
rebuild run on that tag. A rebuild requested from a branch is redirected the same way.
The rebuild therefore uses the tag's workflow and files, and its image provenance
records the tag. It uses the pinned
Debian base image and refreshes its packages. Checksum-verified code-server and Envoy
download stages remain cacheable. It republishes
that release's version tag and `latest` only after both architectures pass all tests.
The build fails without publishing if no stable release exists or its tag is invalid.
If the latest release changes during testing, the older release receives only its
version tag; `latest` is kept for the current release. CI covers full startup, home persistence,
service recovery, and a local backup/restore round trip.

To publish, update the `packages.code-server.version` entry in `build/versions.json` (for example, `4.141.0`) and push
a matching tag, such as `v4.141.0`:

```sh
git tag v4.141.0
git push origin v4.141.0
```

The tagged commit must contain this workflow. After checks pass, CI creates a
GitHub prerelease with generated notes, installer assets, and checksums. It never
automatically promotes a new tag to latest. Failed runs can be rerun; existing
release assets are refreshed without changing its promotion state.

CI, `just build`, and
Compose builds use Dockerfile build-arg defaults synchronized from this file with
`just sync-pins`. CI rejects invalid versions
and mismatched release tags before building. Manually triggered runs on tags
also require a matching version. Only `v*` tags trigger publication; release tags must match the pinned version.

After both architectures pass, the release publishes
`ghcr.io/<owner>/<repository>:4.141.0`, by assembling the tested image digests. Tested images are uploaded by digest only;
a single reusable `staging` tag holds the tested manifest during attestation. The test jobs have read-only repository
permissions. On publishing runs, they transfer the exact tested images as one-day
artifacts to a single publish job with package write permission; images are not rebuilt.
These image artifacts use GitHub Actions artifact storage. This Docker-load publication path does not generate BuildKit provenance or SBOM attestations. New tag pushes publish only the version tag.
Weekly rebuilds update `latest` only for the currently approved stable release. Stable version
tags receive weekly Debian dependency rebuilds, so they can point to new image
contents without a code-server version change. Pin an image digest if you need
fixed contents. To apply a rebuild, run `manage configure --pull --start` and `sudo` if you installed with `sudo`. This runs
`docker compose create --pull always`, followed by `docker compose start`: it checks the registry even when the tag is
already available locally, and recreates containers when the pulled image changes,
preserving mounted volumes. This applies to both `:latest` and version tags such
as `:4.141.0-1`. A reference containing `@sha256:…` stays fixed to that digest;
change the reference to receive a rebuild. A plain container restart does not
pull images. Image names are
derived from the repository and lowercased automatically. Publishing uses
`GITHUB_TOKEN`; no registry secret or naming variables are needed.

For project-only fixes, keep the `packages.code-server.version` entry in `build/versions.json` unchanged and push a
tag such as `v4.141.0-1`, then `v4.141.0-2` for the next fix.
The numeric revision must be at least 1, with no leading zeros. These releases
publish the exact image version (`4.141.0-1`) and become eligible
for weekly rebuilds after promotion. The base version must still match the version file.

To test a prerelease on an installed deployment:

```sh
./manage upgrade --version v4.141.0-1
./manage configure --pull --start
```

Use `sudo` if installed as root. Older installed managers need this version of
`manage.py` before they accept `upgrade --version`; use `manage install --version`
with the deployment's `--directory` to refresh an older installation first.

Once testing is complete, open **Actions → Promote release → Run workflow** on
`main` and enter the full tag, such as `v4.141.0-1`. This checks that the release
has all installer assets and resolves its versioned image digest. It points the
container's `:latest` at that existing digest, then marks the GitHub release stable
and latest. There is no rebuild. Use this workflow to update both latest pointers.
The GitHub and registry updates are separate; if promotion fails between them,
rerun the workflow for the same tag to finish. Plain `manage upgrade` and weekly
rebuilds follow the approved release. RC tag suffixes are not supported.

After the first publish, open the package's **Package settings → Change visibility**
and select **Public** so users can pull without signing in.

