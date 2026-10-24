#!/bin/sh
# Check shell syntax and Compose configurations without using .env.
set -eu
cd -- "$(dirname -- "$0")/../.."

for script in manage build/tools/*.sh build/tests/*.sh; do sh -n "$script"; done
for script in build/runtime/*.sh; do bash -n "$script"; done
command -v shellcheck >/dev/null || { echo 'Install shellcheck to run checks.' >&2; exit 1; }
shellcheck --severity=style manage build/tools/*.sh build/tests/*.sh build/runtime/*.sh
python3 build/tests/test_manage.py
python3 build/tests/test_release.py
python3 build/tests/test_actions.py
python3 build/tests/test_backup_key.py
sh -n build/runtime/homebrew.sh

# Configuration checks use a disposable passphrase, never a deployment key.
export GPG_PASSPHRASE=validation-only

# The base file must work with only the tunnel token set.
CLOUDFLARE_TUNNEL_TOKEN=validation-only docker compose --env-file /dev/null config --quiet
for backup in "" compose.backup.yaml compose.backup-local.yaml; do
    for build in "" compose.build.yaml; do
        set -- -f compose.yaml
        for file in $build $backup; do set -- "$@" -f "$file"; done
        docker compose --env-file .env.example "$@" config --quiet
    done
done

# Backup stop labels, S3 prefixes, and archive names are scoped to the Compose project.
python3 - <<'PY'
import json, os, subprocess

def model(overlay, project="code-server"):
    return json.loads(subprocess.check_output([
        "docker", "compose", "--project-name", project, "--env-file", ".env.example",
        "-f", "compose.yaml", "-f", overlay, "config", "--format", "json",
    ], text=True))

# Both destinations fail closed when encryption is not configured.
for overlay in ("compose.backup.yaml", "compose.backup-local.yaml"):
    result = subprocess.run([
        "docker", "compose", "--env-file", ".env.example", "-f", "compose.yaml",
        "-f", overlay, "config", "--quiet",
    ], env={**os.environ, "GPG_PASSPHRASE": ""}, capture_output=True, text=True)
    assert result.returncode != 0 and "GPG_PASSPHRASE" in result.stderr

deployments = []
for project in ("isolation-a", "isolation-b"):
    s3 = model("compose.backup.yaml", project)
    backup = s3["services"]["backup"]["environment"]
    label = s3["services"]["code-server"]["labels"]["docker-volume-backup.stop-during-backup"]
    assert label == backup["BACKUP_STOP_DURING_BACKUP_LABEL"]
    deployments.append((label, backup["AWS_S3_PATH"], backup["BACKUP_FILENAME"]))
assert all(left != right for left, right in zip(*deployments))

local = model("compose.backup-local.yaml")
backup = local["services"]["backup"]
assert backup["environment"]["GPG_PASSPHRASE"] == "validation-only"
assert model("compose.backup.yaml")["services"]["backup"]["environment"]["GPG_PASSPHRASE"] == "validation-only"
assert backup["environment"]["EXEC_LABEL"] == backup["labels"]["docker-volume-backup.exec-label"]
assert "chown 0:0" in backup["labels"]["docker-volume-backup.copy-post"]
assert "chmod 700 /archive" in backup["labels"]["docker-volume-backup.copy-pre"]
assert "chmod 600" in backup["labels"]["docker-volume-backup.copy-post"]
assert not any(key.startswith("AWS_") for key in backup["environment"])
archive_mount = next(v for v in backup["volumes"] if v["target"] == "/archive")
assert archive_mount["type"] == "bind"
# Compose 5 may omit this field when it has the default value (true).
assert archive_mount.get("bind", {}).get("create_host_path", True) is True
assert backup["environment"]["BACKUP_STOP_DURING_BACKUP_LABEL"] == local["services"]["code-server"]["labels"]["docker-volume-backup.stop-during-backup"]
PY
echo 'Syntax and Compose checks passed.'
