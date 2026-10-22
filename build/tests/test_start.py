"""Check launcher arguments without starting containers."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "start.sh"


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.deployment = self.root / "deployment with spaces"
        self.deployment.mkdir()
        self.script = self.deployment / "start.sh"
        self.script.write_bytes(SCRIPT.read_bytes())
        for name in (".env", "compose.yaml",
                     "compose.backup.yaml", "compose.backup-local.yaml"):
            (self.deployment / name).touch()
        self.log = self.root / "arguments"
        docker = self.root / "docker"
        docker.write_text(
            '#!/usr/bin/env python3\nimport json, os, sys\n'
            'from pathlib import Path\n'
            'Path(os.environ["ARGUMENT_LOG"]).write_text(json.dumps(sys.argv[1:]))\n'
            'sys.exit(int(os.environ.get("DOCKER_EXIT", "0")))\n'
        )
        docker.chmod(0o755)
        self.env = {**os.environ, "PATH": f'{self.root}:{os.environ["PATH"]}',
                    "ARGUMENT_LOG": str(self.log)}

    def run_launcher(self, *args):
        return subprocess.run(
            ["bash", str(self.script), *args],
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

    def test_foreground_pull_and_project(self):
        result = self.run_launcher("--foreground", "--pull", "--project-name", "example")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(self.log.read_text())
        self.assertIn("example", args)
        self.assertNotIn("-d", args)
        self.assertEqual(args[-3:], ["up", "--pull", "always"])

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

    def test_help(self):
        result = self.run_launcher("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertFalse(self.log.exists())

    def test_docker_exit_status(self):
        self.env["DOCKER_EXIT"] = "17"
        self.assertEqual(self.run_launcher().returncode, 17)


if __name__ == "__main__":
    unittest.main()
