"""Check deployment management commands without starting containers."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "manage"


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Match the launcher's physical paths (macOS /var aliases /private/var).
        self.root = Path(self.temp.name).resolve()
        self.deployment = self.root / "deployment with spaces"
        self.deployment.mkdir()
        self.script = self.deployment / "manage"
        self.script.write_bytes(SCRIPT.read_bytes())
        (self.deployment / "manage.py").write_bytes((SCRIPT.parent / "manage.py").read_bytes())
        (self.deployment / "backup-key.py").write_bytes(
            (SCRIPT.parent / "build/tools/backup-key.py").read_bytes())
        (self.deployment / "restore-home.sh").write_bytes(
            (SCRIPT.parent / "build/tools/restore-home.sh").read_bytes())
        for name in (".env", "compose.yaml",
                     "compose.backup-common.yaml", "compose.backup.yaml", "compose.backup-local.yaml"):
            (self.deployment / name).touch()
        self.log = self.root / "arguments"
        docker = self.root / "docker"
        docker.write_text(
            '#!/usr/bin/env python3\nimport json, os, sys\n'
            'from pathlib import Path\n'
            'Path(os.environ["ARGUMENT_LOG"]).write_text(json.dumps(sys.argv[1:]))\n'
            'Path(os.environ["ARGUMENT_LOG"] + ".env").write_text(json.dumps({k: os.environ.get(k) for k in ("BACKUP_LOCAL_UID", "BACKUP_LOCAL_GID", "CODE_SERVER_MEMORY_LIMIT")}))\n'
            'if sys.argv[1:] == ["info", "--format", "{{.MemTotal}}"]: print(os.environ.get("TEST_HOST_MEMORY", "10737418240"))\n'
            'if sys.argv[-3:] == ["config", "--format", "json"]: print(os.environ.get("TEST_MODEL", ""))\n'
            'sys.exit(int(os.environ.get("DOCKER_EXIT", "0")))\n'
        )
        docker.chmod(0o755)
        self.env = {**os.environ, "PATH": f'{self.root}:{os.environ["PATH"]}',
                    "ARGUMENT_LOG": str(self.log)}

    def run_launcher(self, *args):
        return subprocess.run(
            ["sh", str(self.script), *args],
            env=self.env, cwd=self.root, capture_output=True, text=True,
        )

    def test_modes(self):
        for mode, overlay in (("none", None), ("s3", "compose.backup.yaml"),
                              ("local", "compose.backup-local.yaml")):
            with self.subTest(mode=mode):
                result = self.run_launcher("--backup", mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = ["compose", "--project-directory", str(self.deployment),
                            "--env-file", str(self.deployment / ".env"),
                            "-f", str(self.deployment / "compose.yaml")]
                if overlay:
                    expected += ["-f", str(self.deployment / "compose.backup-common.yaml"),
                                 "-f", str(self.deployment / overlay)]
                expected += ["up", "--remove-orphans", "-d"]
                self.assertEqual(json.loads(self.log.read_text()), expected)

    def test_stop_preserves_volumes_and_does_not_generate_key(self):
        for mode in ("none", "local", "s3"):
            with self.subTest(mode=mode):
                result = self.run_launcher("stop", "--backup", mode, "--project-name", "example")
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads(self.log.read_text())
                self.assertEqual(args[-2:], ["down", "--remove-orphans"])
                self.assertIn("example", args)
                self.assertNotIn("-v", args)
                self.assertNotIn("--volumes", args)
                self.assertEqual((self.deployment / ".env").read_text(), "")

    def test_local_backup_owner(self):
        for sudo in (False, True):
            with self.subTest(sudo=sudo):
                self.env.pop("SUDO_UID", None)
                self.env.pop("SUDO_GID", None)
                if sudo:
                    self.env.update(SUDO_UID="1234", SUDO_GID="5678")
                result = self.run_launcher("--backup", "local")
                self.assertEqual(result.returncode, 0, result.stderr)
                owner = json.loads(Path(str(self.log) + ".env").read_text())
                self.assertEqual(owner.pop('CODE_SERVER_MEMORY_LIMIT'), '8589934592')
                self.assertEqual(owner, {
                    "BACKUP_LOCAL_UID": str(os.getuid()),
                    "BACKUP_LOCAL_GID": str(os.getgid()),
                })

    def test_workspace_memory_uses_docker_host_and_rejects_invalid_totals(self):
        self.env['TEST_HOST_MEMORY'] = '1000000000'
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        environment = json.loads(Path(str(self.log) + '.env').read_text())
        self.assertEqual(environment['CODE_SERVER_MEMORY_LIMIT'], '800000000')
        for value in ('0', '-1', 'invalid'):
            self.env['TEST_HOST_MEMORY'] = value
            result = self.run_launcher()
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('up', json.loads(self.log.read_text()))

    def test_foreground_pull_and_project(self):
        result = self.run_launcher("--foreground", "--pull", "--project-name", "example")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(self.log.read_text())
        self.assertIn("example", args)
        self.assertNotIn("-d", args)
        self.assertEqual(args[-4:], ["up", "--remove-orphans", "--pull", "always"])

    def test_release_warning_is_best_effort(self):
        # Exercise the full launcher, replacing only the upstream network response.
        (self.root / "sitecustomize.py").write_text(
            "import io, json, os, urllib.request\n"
            "def release(request, timeout):\n"
            "    assert timeout == 2\n"
            "    if os.environ.get('TEST_OFFLINE'): raise OSError('offline')\n"
            "    assert request.full_url == 'https://api.github.com/repos/weeks-consulting/code-server-aio/releases/latest'\n"
            "    return io.BytesIO(os.environ.get('TEST_RELEASE', '{\"tag_name\": \"v9.0.0\"}').encode())\n"
            "urllib.request.urlopen = release\n")
        self.env["PYTHONPATH"] = str(self.root)
        for tag, offline, warn in (("1.0.0", False, True), ("9.0.0", False, False),
                                   ("1.0.0", True, False), ("latest", False, False)):
            with self.subTest(tag=tag, offline=offline):
                self.env["TEST_MODEL"] = json.dumps({"services": {"code-server": {
                    "image": "ghcr.io/weeks-consulting/code-server-aio:" + tag}}})
                if offline:
                    self.env["TEST_OFFLINE"] = "1"
                else:
                    self.env.pop("TEST_OFFLINE", None)
                result = self.run_launcher("--pull")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("differs from latest release" in result.stderr, warn)
                self.assertEqual(json.loads(self.log.read_text())[-5:], ["up", "--remove-orphans", "-d", "--pull", "always"])

        # Invalid or non-stable upstream responses must not warn or block startup.
        self.env.pop("TEST_OFFLINE", None)
        self.env["TEST_MODEL"] = json.dumps({"services": {"code-server": {
            "image": "ghcr.io/weeks-consulting/code-server-aio:1.0.0"}}})
        for release in ({"tag_name": "invalid"}, {"tag_name": "v9.0.0", "draft": True},
                        {"tag_name": "v9.0.0", "prerelease": True}):
            with self.subTest(release=release):
                self.env["TEST_RELEASE"] = json.dumps(release)
                result = self.run_launcher("--pull")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn("differs from latest release", result.stderr)
                self.assertEqual(json.loads(self.log.read_text())[-2:], ["--pull", "always"])

    def test_upgrade_needs_no_github_account(self):
        # Refresh details and failure paths are covered by test_install's upgrade tests.
        import hashlib
        repository = SCRIPT.parent
        sources = ["compose.yaml", "compose.backup-common.yaml", "compose.backup.yaml",
                   "compose.backup-local.yaml", "manage", "manage.py", ".env.example",
                   "build/tools/restore-home.sh", "build/tools/backup-key.py"]
        (self.root / "SHA256SUMS").write_text("".join(
            hashlib.sha256((repository / name).read_bytes()).hexdigest() + "  " + name + "\n" for name in sources))
        # Model a regular user even when CI runs as root: root-only destination
        # checks reject the world-writable temp directory and are tested in test_install.
        (self.root / "sitecustomize.py").write_text(
            "import io, json, os, pathlib, urllib.request\n"
            "os.geteuid = lambda: 1000\n"
            f"root, repository = pathlib.Path({str(self.root)!r}), pathlib.Path({str(repository)!r})\n"
            "def respond(request, timeout):\n"
            "    assert 'Authorization' not in request.headers\n"
            "    url = request.full_url\n"
            "    if url == 'https://api.github.com/repos/example/fork/releases/latest':\n"
            "        return io.BytesIO(json.dumps({'tag_name': 'v9.0.0-1', 'assets': [{'name': 'SHA256SUMS'}],\n"
            "            'html_url': 'https://github.com/Example/Fork/releases/tag/v9.0.0-1'}).encode())\n"
            "    if url == 'https://github.com/Example/Fork/releases/download/v9.0.0-1/SHA256SUMS':\n"
            "        return io.BytesIO((root / 'SHA256SUMS').read_bytes())\n"
            "    prefix = 'https://raw.githubusercontent.com/Example/Fork/refs/tags/v9.0.0-1/'\n"
            "    assert url.startswith(prefix), url\n"
            "    return io.BytesIO((repository / url[len(prefix):]).read_bytes())\n"
            "urllib.request.urlopen = respond\n")
        # No GitHub CLI or login; package managers must never run.
        for name, body in {"gh": f"touch {str(self.root / 'gh-called')!r}; exit 99", "gpg": "exit 0",
                           "dpkg-query": "printf 'install ok installed\\n'",
                           "apt-get": "exit 99", "brew": "exit 99", "sudo": "exit 99"}.items():
            (self.root / name).write_text("#!/bin/sh\n" + body + "\n")
            (self.root / name).chmod(0o755)
        self.env["PYTHONPATH"] = str(self.root)
        self.env.pop("GH_TOKEN", None)
        self.env.pop("GITHUB_TOKEN", None)
        dotenv = self.deployment / ".env"
        dotenv.write_text("# Keep my settings\n"
                          "export CODE_SERVER_IMAGE='ghcr.io/example/fork:1.0.0'\n"
                          "SECRET=$(never-execute-this)\n")
        result = self.run_launcher("upgrade")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = dotenv.read_text()
        self.assertIn("# Keep my settings\n", text)
        self.assertIn("SECRET=$(never-execute-this)\n", text)
        self.assertIn("CODE_SERVER_IMAGE=ghcr.io/example/fork:9.0.0-1\n", text)
        self.assertEqual(dotenv.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.deployment / "compose.yaml").read_bytes(), (repository / "compose.yaml").read_bytes())
        self.assertIn("manage --pull", result.stdout)
        self.assertFalse((self.root / "gh-called").exists())

    def test_start_without_pull_skips_network(self):
        (self.root / "sitecustomize.py").write_text(
            "import pathlib, urllib.request\n"
            f"def fail(*a, **k): pathlib.Path({str(self.root / 'network-called')!r}).touch(); raise OSError('offline')\n"
            "urllib.request.urlopen = fail\n")
        self.env['PYTHONPATH'] = str(self.root)
        self.env['TEST_MODEL'] = json.dumps({'services': {'code-server': {
            'image': 'ghcr.io/example/app:1.0.0'}}})
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'network-called').exists())

    def test_upgrade_rejects_custom_registry_and_launch_options(self):
        dotenv = self.deployment / ".env"
        original = "CODE_SERVER_IMAGE=example/custom:fixed\n"
        dotenv.write_text(original)
        for args in (("upgrade",), ("upgrade", "--pull"),
                     ("upgrade", "--backup", "local"), ("upgrade", "--project-name", "other")):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_launcher(*args).returncode, 0)
                self.assertEqual(dotenv.read_text(), original)
                self.assertFalse(self.log.exists())

    def test_invalid_options(self):
        for args in (("--backup", "invalid"), ("--backup",), ("--unknown",),
                     ("--directory", str(self.deployment)), ("--build",)):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_launcher(*args).returncode, 0)
                self.assertFalse(self.log.exists())

    def test_missing_files(self):
        for name, args in ((".env", ()), ("compose.backup.yaml", ("--backup", "s3"))):
            path = self.deployment / name
            path.unlink()
            self.assertNotEqual(self.run_launcher(*args).returncode, 0)
            self.assertFalse(self.log.exists())
            path.touch()

    def test_unreadable_env_explains_permissions(self):
        if os.geteuid() == 0:
            self.skipTest("root can read files regardless of their read permission bits")
        dotenv = self.deployment / ".env"
        dotenv.chmod(0)
        self.addCleanup(dotenv.chmod, 0o600)
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sudo", result.stderr)
        self.assertFalse(self.log.exists())

    def test_command_after_options(self):
        result = self.run_launcher("--backup", "local", "backup", "--project-name", "example")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(self.log.read_text())
        self.assertEqual(args[-4:], ["exec", "-T", "backup", "backup"])
        self.assertIn("example", args)
        result = self.run_launcher("start", "stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments", result.stderr)

    def test_help(self):
        result = self.run_launcher("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout)
        self.assertFalse(self.log.exists())

    def test_docker_exit_status(self):
        self.env["DOCKER_EXIT"] = "17"
        self.assertEqual(self.run_launcher().returncode, 17)

    def test_manual_backup(self):
        for mode in ("s3", "local"):
            with self.subTest(mode=mode):
                result = self.run_launcher("backup", "--backup", mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads(self.log.read_text())
                self.assertEqual(args[-4:], ["exec", "-T", "backup", "backup"])

    def test_manual_backup_propagates_service_failure_without_fallback(self):
        self.env["DOCKER_EXIT"] = "17"
        result = self.run_launcher("backup", "--backup", "local")
        self.assertEqual(result.returncode, 17)
        self.assertEqual(json.loads(self.log.read_text())[-4:], ["exec", "-T", "backup", "backup"])

    def test_manual_backup_does_not_generate_a_key_unused_by_the_running_service(self):
        dotenv = self.deployment / ".env"
        dotenv.write_text("BACKUP_GPG_PASSPHRASE=\n")
        result = self.run_launcher("backup", "--backup", "local")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dotenv.read_text(), "BACKUP_GPG_PASSPHRASE=\n")
        self.assertFalse((self.deployment / ".env.lock").exists())

    def test_backup_key_generated_and_preserved(self):
        dotenv = self.deployment / ".env"
        dotenv.write_text("BACKUP_GPG_PASSPHRASE=\n")
        result = self.run_launcher("--backup", "local")
        self.assertEqual(result.returncode, 0, result.stderr)
        contents = dotenv.read_text()
        self.assertRegex(contents, r"BACKUP_GPG_PASSPHRASE=[a-f0-9]{64}")
        result = self.run_launcher("backup", "--backup", "local")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dotenv.read_text(), contents)

    def test_list_local_backups(self):
        result = self.run_launcher("backups", "--backup", "local")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(self.log.read_text())
        self.assertIn(str(self.deployment / "compose.backup-local.yaml"), args)
        self.assertIn("find /archive", args[-1])
        self.assertIn("--no-deps", args)

    def test_restore_relative_to_caller(self):
        archive = self.root / "saved backup.tar.gz"
        archive.touch()
        (self.deployment / ".env").unlink()
        # This test checks launcher argument forwarding; the Docker integration
        # test exercises extraction, rollback encryption, and replacement.
        (self.deployment / "restore-home.sh").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGUMENT_LOG"\n')
        alias = self.root / "caller-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        for caller in (self.root, alias):
            with self.subTest(caller=caller):
                result = subprocess.run(
                    ["sh", str(self.script), "restore", "--archive", archive.name,
                     "--volume", "new-home"],
                    env={**self.env, "PWD": str(caller)}, cwd=caller,
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                forwarded, volume = self.log.read_text().splitlines()
                self.assertTrue(Path(forwarded).is_absolute())
                # macOS /var is a symlink to /private/var; either spelling
                # must identify the archive relative to the caller's directory.
                self.assertEqual(Path(forwarded).resolve(), archive.resolve())
                self.assertEqual(volume, "new-home")

    def test_restore_decodes_compose_dollar_escaping(self):
        archive = self.root / "saved.tar.gz.gpg"
        archive.touch()
        (self.deployment / "restore-home.sh").write_text(
            '#!/bin/sh\nprintf "%s" "$BACKUP_GPG_PASSPHRASE" > "$ARGUMENT_LOG"\n')
        # `docker compose config` writes the literal key pa$$word as pa$$$$word.
        model = json.dumps({"services": {"restore-key": {"environment": {
            "BACKUP_GPG_PASSPHRASE": "pa$$$$word"}}}})
        env = {key: value for key, value in self.env.items() if key != "BACKUP_GPG_PASSPHRASE"}
        result = subprocess.run(
            ["sh", str(self.script), "restore", "--archive", str(archive), "--volume", "home"],
            env={**env, "TEST_MODEL": model}, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text(), "pa$$word")

    def test_invalid_commands(self):
        for args in (("backup",), ("backups", "--backup", "s3"), ("restore",),
                     ("backup", "--backup", "local", "--pull"),
                     ("stop", "--pull"), ("stop", "--foreground"),
                     ("stop", "--volume", "existing"),
                     ("start", "--volume", "existing"), ("unknown",)):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_launcher(*args).returncode, 0)
                self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
