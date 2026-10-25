"""Check deployment management commands without starting containers."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "manage"


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.deployment = self.root / "deployment with spaces"
        self.deployment.mkdir()
        self.script = self.deployment / "manage"
        self.script.write_bytes(SCRIPT.read_bytes())
        (self.deployment / "backup-key.sh").write_bytes(
            (SCRIPT.parent / "build/tools/backup-key.sh").read_bytes())
        (self.deployment / "restore-home.sh").write_bytes(
            (SCRIPT.parent / "build/tools/restore-home.sh").read_bytes())
        for name in (".env", "compose.yaml",
                     "compose.backup.yaml", "compose.backup-local.yaml"):
            (self.deployment / name).touch()
        self.log = self.root / "arguments"
        docker = self.root / "docker"
        docker.write_text(
            '#!/usr/bin/env python3\nimport json, os, sys\n'
            'from pathlib import Path\n'
            'Path(os.environ["ARGUMENT_LOG"]).write_text(json.dumps(sys.argv[1:]))\n'
            'Path(os.environ["ARGUMENT_LOG"] + ".env").write_text(json.dumps({k: os.environ.get(k) for k in ("LOCAL_BACKUP_UID", "LOCAL_BACKUP_GID")}))\n'
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
                    expected += ["-f", str(self.deployment / overlay)]
                expected += ["up", "-d"]
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
                self.assertEqual(owner, {
                    "LOCAL_BACKUP_UID": str(os.getuid()),
                    "LOCAL_BACKUP_GID": str(os.getgid()),
                })

    def test_foreground_pull_and_project(self):
        result = self.run_launcher("--foreground", "--pull", "--project-name", "example")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(self.log.read_text())
        self.assertIn("example", args)
        self.assertNotIn("-d", args)
        self.assertEqual(args[-3:], ["up", "--pull", "always"])

    def test_release_warning_is_best_effort(self):
        # Exercise the full launcher, replacing only the upstream network response.
        (self.root / "sitecustomize.py").write_text(
            "import io, json, os, urllib.request\n"
            "def release(request, timeout):\n"
            "    assert timeout == 2\n"
            "    if os.environ.get('TEST_OFFLINE'): raise OSError('offline')\n"
            "    assert request.full_url == 'https://api.github.com/repos/weeks-consulting/code-server-aio/releases/latest'\n"
            "    return io.BytesIO(json.dumps({'tag_name': 'v9.0.0'}).encode())\n"
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
                self.assertEqual(json.loads(self.log.read_text())[-4:], ["up", "-d", "--pull", "always"])

    def test_upgrade_updates_only_image_and_does_not_start_services(self):
        (self.root / "sitecustomize.py").write_text(
            "import io, json, os, urllib.request\n"
            "def release(request, timeout):\n"
            "    assert request.full_url == 'https://api.github.com/repos/example/fork/releases/latest'\n"
            "    assert timeout == 15\n"
            "    if os.environ.get('TEST_OFFLINE'): raise OSError('offline')\n"
            "    return io.BytesIO(json.dumps({'tag_name': os.environ.get('TEST_TAG', 'v9.0.0-1')}).encode())\n"
            "urllib.request.urlopen = release\n")
        self.env["PYTHONPATH"] = str(self.root)
        dotenv = self.deployment / ".env"
        original = ("# Keep my settings\n"
                    "export CODE_SERVER_IMAGE='ghcr.io/example/fork:1.0.0'\n"
                    "SECRET=$(never-execute-this)\n")
        dotenv.write_text(original)
        result = self.run_launcher("upgrade")
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = original.replace("export CODE_SERVER_IMAGE='ghcr.io/example/fork:1.0.0'",
                                    "CODE_SERVER_IMAGE=ghcr.io/example/fork:9.0.0-1")
        self.assertEqual(dotenv.read_text(), expected)
        self.assertEqual(dotenv.stat().st_mode & 0o777, 0o600)
        self.assertFalse(self.log.exists())
        self.assertIn("manage --pull", result.stdout)
        for overrides in ({"TEST_OFFLINE": "1"}, {"TEST_TAG": "invalid"}):
            with self.subTest(overrides=overrides):
                self.env.update(overrides)
                result = self.run_launcher("upgrade")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(dotenv.read_text(), expected)
                for key in overrides:
                    self.env.pop(key)

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
        self.assertEqual(args[-6:], ["run", "--rm", "--no-deps", "--entrypoint", "backup", "backup"])
        self.assertIn("example", args)
        result = self.run_launcher("start", "stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("only one command", result.stderr)

    def test_help(self):
        result = self.run_launcher("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
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
                self.assertEqual(args[-6:], ["run", "--rm", "--no-deps", "--entrypoint", "backup", "backup"])

    def test_backup_key_generated_and_preserved(self):
        dotenv = self.deployment / ".env"
        dotenv.write_text("GPG_PASSPHRASE=\n")
        result = self.run_launcher("--backup", "local")
        self.assertEqual(result.returncode, 0, result.stderr)
        contents = dotenv.read_text()
        self.assertRegex(contents, r"GPG_PASSPHRASE=[a-f0-9]{64}")
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
        result = self.run_launcher("restore", "--archive", archive.name, "--volume", "new-home")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(self.log.read_text())
        self.assertEqual(args[args.index("--user") + 1], "0:0")
        self.assertIn(f"ARCHIVE_UID={os.getuid()}", args)
        self.assertIn(f"ARCHIVE_GID={os.getgid()}", args)
        self.assertIn(f"type=bind,src={archive.resolve()},dst=/backup.tar.gz,readonly", args)
        self.assertIn("type=volume,src=new-home,dst=/restore", args)
        self.assertIn("Original volume backup complete; replacing volume contents.", args[-1])

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
