# Restoring a backup

Download the desired `.tar.gz` archive from your S3 bucket using the console or
your S3 client. Use the configured `AWS_S3_PATH`; old backups keep their original
filenames and locations.

Restore into a new, unused Docker volume. The helper refuses to overwrite a
non-empty volume and preserves stored file ownership:

```sh
bash build/backup/restore-home.sh /absolute/path/to/backup.tar.gz restored-home
```

Inspect the restored home directory before switching:

```sh
docker run --rm -v restored-home:/restore:ro debian:13-slim \
  ls -la /restore/cloud-user
```

The archive layout is `/backup/code-server-home/`, containing the entire `/home`
tree. Archives made before the `/home` mount change need their user files moved
into `cloud-user/` after restoration. Homebrew binaries should be restored onto
the same CPU architecture.

To switch safely, first stop the existing stack using the same Compose files
used to start it. Keep its original home volume for rollback. Create
`compose.restore.yaml` with:

```yaml
volumes:
  code-server-home:
    external: true
    name: restored-home
```

Start with the restore override added last; include build/backup overlays if used:

```sh
docker compose -f compose.yaml -f compose.restore.yaml up -d
# Local development with backups:
docker compose -f compose.yaml -f compose.build.yaml -f compose.backup.yaml \
  -f compose.restore.yaml up -d --build
```

Verify files, extensions, Homebrew, and sign-in before deleting any old volume.
Keep using the restore override for subsequent commands. To roll back, stop the
restored stack and restart with the original Compose file combination.

Exercise archive creation and restoration locally without S3:

```sh
docker build -f build/Dockerfile -t code-server-cloudflare:test .
bash build/tests/backup-restore.sh code-server-cloudflare:test
```

This verifies archive layout, hidden files, shared home directories, ownership,
and refusal to overwrite data. It does not verify your S3 credentials or uploads.
