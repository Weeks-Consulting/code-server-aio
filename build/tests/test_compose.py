"""Validate resolved Compose configuration without starting containers."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
RUN_MODE_LABEL = 'io.github.weeks-consulting.code-server-aio.run-mode'


def backup_environment(backup, archive):
    # Undo Compose's serialization escaping before running the shell on the host.
    env = {'PATH': os.environ['PATH'], **{
        name: value.replace('$$', '$') for name, value in backup['environment'].items()
    }}
    if 'BACKUP_LOCAL_CHECK' in env:
        env['BACKUP_LOCAL_CHECK'] = env['BACKUP_LOCAL_CHECK'].replace('archive="/archive"',
                                                                     f'archive={shlex.quote(str(archive))}')
        # The Alpine sidecar uses stat -c, including when the client runs on macOS.
        stat_tool = archive.parent / 'stat'
        stat_tool.write_text('#!/usr/bin/env python3\nimport os, stat, sys\n'
                             'info = os.lstat(sys.argv[3])\n'
                             'if sys.argv[2] == "%h": print(info.st_nlink)\n'
                             'else: print(f"{info.st_uid}:{info.st_gid}:{stat.S_IMODE(info.st_mode):o}")\n')
        stat_tool.chmod(0o755)
        env['PATH'] = str(stat_tool.parent) + os.pathsep + env['PATH']
    return env


class ComposeTests(unittest.TestCase):
    def test_configuration_contracts(self):
        previous = Path.cwd()
        os.chdir(ROOT)
        self.addCleanup(os.chdir, previous)
        valid_key = '0123456789abcdef' * 4
        with patch.dict(os.environ, {"BACKUP_GPG_PASSPHRASE": valid_key, "BACKUP_RETENTION_DAYS": "",
                                     "BACKUP_AWS_S3_PATH": ""}):

            def model(overlay, project="code-server", backup_id=""):
                return json.loads(subprocess.check_output([
                    "docker", "compose", "--project-name", project, "--env-file", ".env.example",
                    "-f", "compose.yaml", "-f", "compose.backup-common.yaml", "-f", overlay, "config", "--format", "json",
                ], text=True, env={**os.environ, "BACKUP_ID": backup_id}))

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
                self.assertEqual(service['labels'][RUN_MODE_LABEL], 'production')
            assert services['backup']['environment']['BACKUP_RETENTION_DAYS'] == '7'
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

            for project, backup_id in (("isolation-a", ""), ("isolation-b", "custom-id")):
                with self.subTest(project=project, backup_id=backup_id):
                    s3 = model("compose.backup.yaml", project, backup_id)
                    backup = s3["services"]["backup"]["environment"]
                    label = s3["services"]["code-server"]["labels"]["docker-volume-backup.stop-during-backup"]
                    deployment_id = backup_id or project
                    assert label == backup["BACKUP_STOP_DURING_BACKUP_LABEL"] == f"{project}-home"
                    assert backup["BACKUP_ID"] == deployment_id
                    assert backup["AWS_S3_PATH"] == f"{deployment_id}/home"
                    assert backup["BACKUP_PRUNING_PREFIX"] == f"home.{deployment_id}."
                    assert backup["BACKUP_FILENAME"] == f"home.{deployment_id}.%Y-%m-%dT%H-%M-%S-%f%z.tar.gz"

            local = model("compose.backup-local.yaml")
            backup = local["services"]["backup"]
            assert backup["environment"]["GPG_PASSPHRASE"] == valid_key
            assert backup["environment"]["BACKUP_PRUNING_PREFIX"] == "home.code-server."
            assert services['backup']['environment']['GPG_PASSPHRASE'] == valid_key
            assert backup["environment"]["EXEC_LABEL"] == backup["labels"]["docker-volume-backup.exec-label"]
            assert backup['environment']['BACKUP_LOCAL_UID'] == os.environ.get('BACKUP_LOCAL_UID', '0')
            assert backup['environment']['BACKUP_LOCAL_GID'] == os.environ.get('BACKUP_LOCAL_GID', '0')
            assert "chmod 600" in backup["labels"]["docker-volume-backup.copy-post"]
            assert not any(key.startswith("AWS_") for key in backup["environment"])
            archive_mount = next(v for v in backup["volumes"] if v["target"] == "/archive")
            assert archive_mount["type"] == "bind"
            assert archive_mount['bind']['create_host_path'] is False
            assert backup["environment"]["BACKUP_STOP_DURING_BACKUP_LABEL"] == local["services"]["code-server"]["labels"]["docker-volume-backup.stop-during-backup"]

    def test_development_override_labels_both_services(self):
        model = json.loads(subprocess.check_output([
            'docker', 'compose', '--project-name', 'development-test', '--env-file', '.env.example',
            '-f', 'compose.yaml', '-f', 'compose.build.yaml', '-f', 'compose.dev.yaml', 'config', '--format', 'json',
        ], cwd=ROOT, text=True))
        self.assertEqual({name: service['labels'][RUN_MODE_LABEL] for name, service in model['services'].items()},
                         {'code-server': 'development', 'cloudflared': 'development'})

    def test_backup_entrypoint_validates_before_running_offen(self):
        valid_key = '0123456789abcdef' * 4
        for overlay in ('compose.backup-local.yaml', 'compose.backup.yaml'):
            with self.subTest(overlay=overlay), tempfile.TemporaryDirectory() as temporary:
                archive = Path(temporary) / 'archive'
                archive.mkdir(mode=0o700)
                (archive / '.code-server-aio-backups').touch()
                model = json.loads(subprocess.check_output([
                    'docker', 'compose', '--project-name', 'entrypoint-test', '--env-file', '.env.example', '-f', 'compose.yaml',
                    '-f', 'compose.backup-common.yaml', '-f', overlay, 'config', '--format', 'json',
                ], cwd=ROOT, text=True, env={**os.environ, 'BACKUP_ID': '', 'BACKUP_GPG_PASSPHRASE': valid_key,
                                            'BACKUP_LOCAL_UID': str(os.getuid()), 'BACKUP_LOCAL_GID': str(os.getgid()),
                                            'BACKUP_LOCAL_PATH': str(archive)}))
                shell, flags, script = model['services']['backup']['entrypoint']
                saved = json.loads(subprocess.check_output([
                    'docker', 'compose', '--project-directory', str(ROOT), '--env-file', '/dev/null',
                    '-f', '-', 'config', '--format', 'json',
                ], input=json.dumps(model), cwd=ROOT, text=True))
                backup = saved['services']['backup']
                self.assertEqual(backup['entrypoint'], [shell, flags, script])
                self.assertEqual(backup['environment'], model['services']['backup']['environment'])
                self.assertEqual(backup.get('labels'), model['services']['backup'].get('labels'))
                env = backup_environment(backup, archive)
                # Undo Compose's serialization escaping and stub only the executable.
                script = script.replace('$$', '$').replace('/usr/bin/backup', '/bin/echo')
                for key in (valid_key, '', valid_key[:-1], valid_key.upper(), valid_key[:-1] + '\n'):
                    result = subprocess.run([shell, flags, script], capture_output=True, text=True,
                                            env={**env, 'GPG_PASSPHRASE': key})
                    if key == valid_key:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(result.stdout, '-foreground\n')
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(result.stdout, '')
                        self.assertIn('64 lowercase hexadecimal characters', result.stderr)

    def test_local_backup_rejects_unprepared_directories_and_existing_destinations(self):
        model = json.loads(subprocess.check_output([
            'docker', 'compose', '--env-file', '.env.example', '-f', 'compose.yaml',
            '-f', 'compose.backup-common.yaml', '-f', 'compose.backup-local.yaml', 'config', '--format', 'json',
        ], cwd=ROOT, text=True, env={**os.environ, 'BACKUP_ID': 'local-test', 'BACKUP_GPG_PASSPHRASE': 'a' * 64,
                                    'BACKUP_LOCAL_UID': str(os.getuid()), 'BACKUP_LOCAL_GID': str(os.getgid())}))
        backup = model['services']['backup']
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / 'archive'
            env = backup_environment(backup, archive)
            env['COMMAND_RUNTIME_ARCHIVE_FILEPATH'] = '/tmp/example.tar.gz.gpg'
            shell, options, startup = backup['entrypoint']
            startup = [shell, options, startup.replace('$$', '$').replace('/usr/bin/backup', '/bin/echo')]

            def hook(phase):
                label = backup['labels'][f'docker-volume-backup.{phase}']
                return shlex.split(label.replace('$$', '$').replace('/archive/', str(archive) + '/'))

            copy_pre, copy_post, prune_pre = (hook(phase) for phase in ('copy-pre', 'copy-post', 'prune-pre'))

            def run(command, **overrides):
                return subprocess.run(command, env={**env, **overrides}, capture_output=True, text=True)

            self.assertNotEqual(run(startup).returncode, 0)
            self.assertFalse(archive.exists())
            archive.mkdir()
            archive.chmod(0o755)
            marker = archive / '.code-server-aio-backups'
            marker.touch()
            for command in (startup, copy_pre, prune_pre):
                result = run(command)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('with mode 700', result.stderr)
            self.assertEqual(archive.stat().st_mode & 0o777, 0o755)
            archive.chmod(0o700)
            marker.unlink()
            self.assertIn('marker file', run(startup).stderr)
            marker.touch()
            self.assertNotEqual(run(startup, BACKUP_LOCAL_UID=str(os.getuid() + 1)).returncode, 0)
            self.assertEqual(run(startup).stdout, '-foreground\n')
            self.assertEqual(run(copy_pre).returncode, 0)
            self.assertEqual(run(prune_pre).returncode, 0)
            target = archive / 'example.tar.gz.gpg'
            target.symlink_to(archive / 'missing')
            result = run(copy_pre)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('archive already exists', result.stderr)
            self.assertNotEqual(run(copy_post).returncode, 0)
            self.assertTrue(target.is_symlink())
            target.unlink()
            target.touch()
            target.chmod(0o644)
            self.assertNotEqual(run(copy_pre).returncode, 0)
            os.link(target, archive / 'hard-link')
            self.assertNotEqual(run(copy_post).returncode, 0)
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            (archive / 'hard-link').unlink()
            result = run(copy_post)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual(archive.stat().st_mode & 0o777, 0o700)
