[← Project overview](../README.md)

## Image publishing

GitHub Actions builds and tests native amd64 and arm64 images on pull requests,
pushes to `main`/`master`, manual runs, weekly scheduled builds, and published
GitHub releases. Published releases and weekly scheduled builds upload images;
regular commits and manual runs run tests only by default. To publish an urgent
rebuild, use **Actions → Build and publish container → Run workflow** and enable
**Rebuild and publish the latest stable release**. This uses the latest stable
release source, bypasses the build cache, and applies the same tests and latest-release
checks as the weekly rebuild. Every Monday at 08:17 UTC
(03:17 in Chicago during daylight saving time, 02:17 during standard time), the
scheduled build checks out the latest stable GitHub release tag, uses the pinned
Debian base image and refreshes its packages, and bypasses cached package layers. It republishes
that release's version tag and `latest` only after both architectures pass all tests.
The build fails without publishing if no stable release exists or its tag is invalid.
If the latest release changes during testing, the older release receives only its
version tag; `latest` is kept for the current release. CI covers full startup, home persistence,
service recovery, and a local backup/restore round trip.

To publish, update the `packages.code-server.version` entry in `build/versions.json` (for example, `4.141.0`) and publish
a GitHub release with the matching tag, such as `v4.141.0`. CI, `just build`, and
Compose builds all read this file. CI rejects invalid versions
and mismatched release tags before building. Manually triggered runs on tags
also require a matching version. Pushing a tag alone does not publish.

After both architectures pass, the release publishes
`ghcr.io/<owner>/<repository>:4.141.0`, by assembling the tested image digests. Tested images are uploaded by digest only;
a single reusable `staging` tag holds the tested manifest during attestation. The test jobs have read-only repository
permissions. On publishing runs, they transfer the exact tested images as one-day
artifacts to separate upload jobs with package write permission; images are not rebuilt.
These image artifacts use GitHub Actions artifact storage. This Docker-load publication path does not generate BuildKit provenance or SBOM attestations. Stable releases
also update `latest`; prereleases publish only the version tag. Stable version
tags receive weekly Debian dependency rebuilds, so they can point to new image
contents without a code-server version change. Pin an image digest if you need
fixed contents. To apply a rebuild, run `manage --pull` with your usual launch
options and `sudo` if you installed with `sudo`. This runs
`docker compose up --pull always`: it checks the registry even when the tag is
already available locally, and recreates containers when the pulled image changes,
preserving mounted volumes. This applies to both `:latest` and version tags such
as `:4.141.0-1`. A reference containing `@sha256:…` stays fixed to that digest;
change the reference to receive a rebuild. A plain container restart does not
pull images. Image names are
derived from the repository and lowercased automatically. Publishing uses
`GITHUB_TOKEN`; no registry secret or naming variables are needed.

For project-only fixes, keep the `packages.code-server.version` entry in `build/versions.json` unchanged and publish a
stable GitHub release such as `v4.141.0-1`, then `v4.141.0-2` for the next fix.
The numeric revision must be at least 1, with no leading zeros. These releases
publish the exact image version (`4.141.0-1`), update `latest`, and are eligible
for weekly rebuilds. The base version must still match the version file.

To publish a test image, mark the GitHub release as a **pre-release**. It publishes
only its version tag and does not update `latest`. RC tag suffixes are not supported.

After the first publish, open the package's **Package settings → Change visibility**
and select **Public** so users can pull without signing in.

