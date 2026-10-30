[← Project overview](../README.md)

## Image verification

Release jobs push each native architecture image after its tests pass, then assemble the recorded registry digests into the published multi-platform image without rebuilding.
The publish job creates signed GitHub build provenance for the resulting index digest
and stores it in GHCR. Before deploying a new image, verify it using:

```sh
gh attestation verify oci://ghcr.io/weeks-consulting/code-server-aio:latest --repo Weeks-Consulting/code-server-aio --signer-workflow Weeks-Consulting/code-server-aio/.github/workflows/container.yaml
```

Weekly rebuilds run on the release tag, so release and rebuilt images can both be
checked against their tag by adding `--source-ref refs/tags/vX.Y.Z`. Rebuilds use that
tag's workflow file, not the current one on `main`.

Use the verified digest in `CODE_SERVER_IMAGE` to deploy exactly those bytes. Tags
remain mutable to support weekly security rebuilds; digest pins require deliberate
updates. Installer reruns preserve existing digest pins and warn that they were kept.
`manage upgrade` deliberately replaces a digest pin with the latest release tag;
verify and set a new digest instead if you want to keep deploying verified bytes.
This optional check needs the GitHub CLI logged in to GitHub (`gh auth login`), even
for public images; deployments themselves never need it. The installers check
downloaded files against `SHA256SUMS` and fail closed. Restrict package publishing to
trusted maintainers.
`manage start --pull` also warns when a version-tagged GHCR image differs from its repository's
latest stable release, with a best-effort check that stays quiet on network errors. Starts without
`--pull` skip the network check.

Release deployment checksums also have GitHub artifact provenance. The installers
do not check it. To verify the checksums' origin yourself with a logged-in GitHub CLI,
download `SHA256SUMS` from the selected release and run:

```sh
gh attestation verify SHA256SUMS --repo Weeks-Consulting/code-server-aio --signer-workflow Weeks-Consulting/code-server-aio/.github/workflows/container.yaml --source-ref refs/tags/vX.Y.Z --deny-self-hosted-runners
```

Checksums downloaded from the same release verify file integrity; independent
provenance verification also verifies the publishing repository, workflow, and release tag.
The current installer requires deployment checksums and the current file layout;
use an older release's own installer when deploying that release.

### Pin and environment migration

`build/versions.json` and `build/build-versions.json` are the sources of truth for shipped
dependencies and build/test tooling respectively. Docker and Actions require literal image
and action references in their configuration: after editing the manifest, run
`just sync-pins` to update those references. `just check` rejects drift, and
`just upgrade` and `just build-upgrade` update their respective manifest and references together. Local builds use the
manifest directly; the former `CODE_SERVER_VERSION` build override has been removed.

For existing deployments, rename `.env` keys before using the updated Compose files:
`CLOUDFLARE_TUNNEL_TOKEN` → `CF_TUNNEL_TOKEN`, `GPG_PASSPHRASE` →
`BACKUP_GPG_PASSPHRASE`, every `AWS_*` key → `BACKUP_AWS_*`, and
`LOCAL_BACKUP_UID/GID` → `BACKUP_LOCAL_UID/GID`. Preserve the existing encryption
key value so older backups remain recoverable. Remove `HOMEBREW_INSTALL_TIMEOUT`.
Compose translates the public backup names into the backup image's required
`GPG_PASSPHRASE`, `AWS_*`, and `TZ` variables and the tunnel token into `TUNNEL_TOKEN`.
