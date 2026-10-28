"""Validate resolved Compose configuration without starting containers."""

import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class ComposeTests(unittest.TestCase):
    def test_configuration_contracts(self):
        previous = Path.cwd()
        os.chdir(ROOT)
        self.addCleanup(os.chdir, previous)
        with patch.dict(os.environ, {"BACKUP_GPG_PASSPHRASE": "validation-only"}):

            def model(overlay, project="code-server"):
                return json.loads(subprocess.check_output([
                    "docker", "compose", "--project-name", project, "--env-file", ".env.example",
                    "-f", "compose.yaml", "-f", "compose.backup-common.yaml", "-f", overlay, "config", "--format", "json",
                ], text=True))

            # Both destinations fail closed when encryption is not configured.
            for overlay in ("compose.backup.yaml", "compose.backup-local.yaml"):
                result = subprocess.run([
                    "docker", "compose", "--env-file", ".env.example", "-f", "compose.yaml",
                    "-f", "compose.backup-common.yaml", "-f", overlay, "config", "--quiet",
                ], env={**os.environ, "BACKUP_GPG_PASSPHRASE": ""}, capture_output=True, text=True)
                assert result.returncode != 0 and "BACKUP_GPG_PASSPHRASE" in result.stderr

            # The protected listener is in the application; backend/admin stay local.
            base = model("compose.backup.yaml")
            services = base["services"]
            app, tunnel = (services[name] for name in ("code-server", "cloudflared"))
            assert int(tunnel['mem_limit']) == 256 * 1024 * 1024
            assert tunnel['depends_on']['code-server']['condition'] == 'service_healthy'
            for service in services.values():
                assert service['logging']['options'] == {'max-file': '3', 'max-size': '10m'}
            assert services['backup']['environment']['BACKUP_RETENTION_DAYS'] == '7'
            assert services['backup']['environment']['BACKUP_PRUNING_PREFIX'] == 'code-server-home-'
            assert "envoy" not in services
            assert not app.get("ports") and app["expose"] == ["8080"]
            assert set(base["networks"]) == {"default"}
            assert set(app["networks"]) == set(tunnel["networks"]) == {"default"}
            assert not any(volume.get("source") == "code-server-socket"
                           for service in services.values() for volume in service.get("volumes", []))
            assert "CF_ACCESS_AUD" in app["environment"]
            assert tunnel["environment"]["TUNNEL_TOKEN"] == "replace-with-your-cloudflare-tunnel-token"
            keys = [line.split("=", 1)[0] for line in Path(".env.example").read_text().splitlines()
                    if line and not line.startswith("#")]
            assert all(key.startswith(("CF_", "BACKUP_", "CODE_SERVER_")) for key in keys)
            assert "HOMEBREW_INSTALL_TIMEOUT" not in app["environment"]

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
