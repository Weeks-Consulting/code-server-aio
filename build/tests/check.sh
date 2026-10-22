#!/bin/bash
# Check shell syntax and Compose configurations without using .env.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.."

for script in build/runtime/*.sh build/tools/*.sh build/tests/*.sh; do bash -n "$script"; done
sh -n build/runtime/homebrew.sh

# The base file must work with only the tunnel token set.
CLOUDFLARE_TUNNEL_TOKEN=validation-only docker compose --env-file /dev/null config --quiet
for backup in "" compose.backup.yaml compose.backup-local.yaml; do
    for build in "" compose.build.yaml; do
        files=(-f compose.yaml)
        for file in $build $backup; do files+=(-f "$file"); done
        docker compose --env-file .env.example "${files[@]}" config --quiet
    done
done

# Backup stop labels, S3 prefixes, and archive names are scoped to the Compose project.
python3 - <<'PY'
import json, subprocess

def model(overlay, project="code-server"):
    return json.loads(subprocess.check_output([
        "docker", "compose", "--project-name", project, "--env-file", ".env.example",
        "-f", "compose.yaml", "-f", overlay, "config", "--format", "json",
    ], text=True))

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
assert not any(key.startswith("AWS_") for key in backup["environment"])
assert any(v["type"] == "bind" and v["target"] == "/archive" for v in backup["volumes"])
assert backup["environment"]["BACKUP_STOP_DURING_BACKUP_LABEL"] == local["services"]["code-server"]["labels"]["docker-volume-backup.stop-during-backup"]
PY
echo 'Syntax and Compose checks passed.'
