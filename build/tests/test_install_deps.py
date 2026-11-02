"""Exercise host dependency setup without package managers or network access."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class InstallDepsTests(unittest.TestCase):
    def test_without_homebrew_reports_all_missing_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp)
            (tools / "uname").write_text('#!/bin/sh\necho Linux\n')
            (tools / "uname").chmod(0o755)
            result = subprocess.run(
                ["/bin/sh", str(ROOT / "build/tools/install-deps.sh")],
                env={**os.environ, "PATH": tmp}, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn('Homebrew is unavailable', result.stderr)
            for dependency in ('docker', 'python3', 'gpg', 'shellcheck', 'curl', 'act',
                               'docker compose', 'docker buildx'):
                self.assertIn('  ' + dependency + '\n', result.stderr)

    def test_homebrew_installs_act_on_linux_and_macos(self):
        for system in ('Linux', 'Darwin'):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                brew_log = root / 'brew.log'
                stubs = {
                    'uname': f'echo {system}',
                    'docker': 'exit 0',
                    'brew': f'echo "$*" >> "{brew_log}"\nif [ "$1" = --prefix ]; then echo "{root}"; fi',
                }
                for name, body in stubs.items():
                    path = root / name
                    path.write_text('#!/bin/sh\n' + body + '\n')
                    path.chmod(0o755)
                result = subprocess.run(
                    ['/bin/sh', str(ROOT / 'build/tools/install-deps.sh')],
                    capture_output=True, text=True,
                    env={**os.environ, 'PATH': str(root), 'HOME': str(root)},
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('install python gnupg shellcheck curl act\n', brew_log.read_text())
