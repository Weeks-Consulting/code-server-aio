"""Check key generation, retention, and safe handling of deployment dotenv files."""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BackupKeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.env = self.directory / ".env"

    def initialize(self):
        return subprocess.run(["bash", str(ROOT / "build/tools/backup-key.sh"), str(self.env)],
                              capture_output=True, text=True)

    def test_missing_and_empty_keys(self):
        for value in ("", "GPG_PASSPHRASE=\n", 'GPG_PASSPHRASE="" # empty\n',
                      "export GPG_PASSPHRASE = ''\n", "GPG_PASSPHRASE= # empty\n"):
            with self.subTest(value=value):
                self.env.write_text("OTHER=unchanged\n" + value)
                result = self.initialize()
                self.assertEqual(result.returncode, 0, result.stderr)
                contents = self.env.read_text()
                key = re.search(r"^GPG_PASSPHRASE=([a-f0-9]{64})$", contents, re.M)[1]
                self.assertIn("OTHER=unchanged\n", contents)
                self.assertEqual(self.env.stat().st_mode & 0o777, 0o600)
                self.assertNotIn(key, result.stdout + result.stderr)
                self.assertEqual(self.initialize().returncode, 0)
                self.assertEqual(self.env.read_text(), contents)

    def test_preserves_custom_key_and_never_executes_dotenv(self):
        marker = self.directory / "must-not-exist"
        contents = f"OTHER=$(touch {marker})\nexport GPG_PASSPHRASE='custom passphrase $literal' # keep\n"
        self.env.write_text(contents)
        self.assertEqual(self.initialize().returncode, 0)
        self.assertEqual(self.env.read_text(), contents)
        self.assertFalse(marker.exists())

    def test_different_deployments_get_different_keys(self):
        self.env.touch()
        self.assertEqual(self.initialize().returncode, 0)
        first = self.env.read_text()
        self.env.write_text("")
        self.assertEqual(self.initialize().returncode, 0)
        self.assertNotEqual(first, self.env.read_text())

    def installer_fixture(self):
        # Serve the workspace's release files without any network requests.
        tools = self.directory / "bin"
        tools.mkdir()
        curl = tools / "curl"
        curl.write_text(
            "#!/usr/bin/env python3\nimport pathlib, shutil, sys\n"
            "args = sys.argv[1:]\n"
            "url = next(a for a in args if a.startswith('https://'))\n"
            "relative = url.split('/refs/tags/v4.141.0-1/', 1)[1]\n"
            f"shutil.copyfile(pathlib.Path({str(ROOT)!r}) / relative, args[args.index('--output') + 1])\n"
            "if '--write-out' in args: print('200', end='')\n"
        )
        curl.chmod(0o755)
        deployment = self.directory / "installed"
        command = ["bash", str(ROOT / "build/tools/install.sh"), "--version", "v4.141.0-1",
                   "--directory", str(deployment)]
        env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"}
        return command, env, deployment

    def test_installer_generates_and_preserves_key_on_refresh(self):
        command, env, deployment = self.installer_fixture()
        for initial in (None, "GPG_PASSPHRASE=\n", "GPG_PASSPHRASE='custom secret'\n"):
            with self.subTest(initial=initial):
                deployment.mkdir(exist_ok=True)
                dotenv = deployment / ".env"
                if initial is None:
                    dotenv.unlink(missing_ok=True)
                else:
                    dotenv.write_text(initial)
                result = subprocess.run(command, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(os.access(deployment / "manage", os.X_OK))
                contents = dotenv.read_text()
                if initial and "custom secret" in initial:
                    self.assertIn(initial, contents)
                else:
                    self.assertRegex(contents, r"GPG_PASSPHRASE=[a-f0-9]{64}")
                self.assertEqual(dotenv.stat().st_mode & 0o777, 0o600)
                result = subprocess.run(command, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(dotenv.read_text(), contents)

    def test_installer_merges_new_settings_and_preserves_existing_lines(self):
        command, env, deployment = self.installer_fixture()
        deployment.mkdir()
        dotenv = deployment / ".env"
        marker = self.directory / "must-not-exist"
        original = ("# My existing configuration\n"
                    "CODE_SERVER_IMAGE=example/custom:fixed\n"
                    "export CF_ACCESS_AUD='custom audience'\n"
                    "export BACKUP_ID = \n"
                    "GPG_PASSPHRASE='already-set'\n"
                    f"CUSTOM=$(touch {marker})\n")
        dotenv.write_text(original)
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        contents = dotenv.read_text()
        self.assertTrue(contents.startswith(original))
        self.assertIn("# Settings added by installer for release v4.141.0-1", contents)
        self.assertIn("# Maximum first-start Homebrew installation duration, in seconds.\nHOMEBREW_INSTALL_TIMEOUT=600", contents)
        for key in ("CODE_SERVER_IMAGE", "CF_ACCESS_AUD", "BACKUP_ID", "GPG_PASSPHRASE"):
            self.assertEqual(len(re.findall(rf"^(?:export )?{key}\s*=", contents, re.M)), 1)
        self.assertFalse(marker.exists())
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dotenv.read_text(), contents)

    def test_installer_rejects_missing_and_option_values(self):
        for option in ("--directory", "--version", "--repository"):
            for value in (None, "", "--version"):
                with self.subTest(option=option, value=value):
                    args = ["bash", str(ROOT / "build/tools/install.sh"), option]
                    if value is not None:
                        args.append(value)
                    result = subprocess.run(args, cwd=self.directory, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f"Missing value for {option}", result.stderr)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_rejects_symlinks(self):
        target = self.directory / "target"
        target.write_text("unchanged")
        self.env.symlink_to(target)
        self.assertNotEqual(self.initialize().returncode, 0)
        self.assertEqual(target.read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
