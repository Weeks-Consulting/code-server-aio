"""Regression coverage for host downloads, startup diagnostics, and restore preflight."""
import ast
import hashlib
import importlib.util
import inspect
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import urllib.error
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('manage', ROOT / 'manage.py')
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class ReviewFixTests(unittest.TestCase):
    def test_bootstrap_and_management_use_identical_cli_downloader(self):
        bootstrap = (ROOT / 'build/tools/install.sh').read_text().split("<<'GH_PY'\n", 1)[1].split('\nGH_PY', 1)[0]
        function = next(node for node in ast.parse(bootstrap).body if isinstance(node, ast.FunctionDef))
        self.assertEqual(ast.dump(function, include_attributes=False),
                         ast.dump(ast.parse(inspect.getsource(manage.ensure_gh)).body[0], include_attributes=False))

    def test_bootstrap_reports_cli_failures_without_a_traceback(self):
        import sys
        bootstrap = (ROOT / 'build/tools/install.sh').read_text().split("<<'GH_PY'\n", 1)[1].split('\nGH_PY', 1)[0]
        handler = ast.parse(bootstrap).body[-1]
        for error in (OSError('download unavailable'), ValueError('unsupported platform'),
                      ValueError('GitHub CLI archive checksum mismatch')):
            with self.subTest(error=error), patch.object(sys, 'argv', ['bootstrap', '/unused']):
                with self.assertRaises(SystemExit) as raised:
                    exec(compile(ast.Module(body=[handler], type_ignores=[]), '<bootstrap>', 'exec'),
                         {'ensure_gh': unittest.mock.Mock(side_effect=error), 'Path': Path, 'sys': sys})
                self.assertEqual(str(raised.exception),
                                 'Could not obtain a GitHub CLI that supports attestation verification: ' + str(error))

    def test_ci_cli_download_hides_existing_cli_and_cleans_up(self):
        spec = importlib.util.spec_from_file_location('check_gh_download', ROOT / 'build/tests/check-gh-download.py')
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)
        destinations = []

        def download(directory):
            self.assertEqual(os.environ['PATH'], '')
            self.assertTrue(directory.is_dir())
            destinations.append(directory)
            return str(directory / 'gh')

        with patch.dict(os.environ, PATH='/original/bin'), \
                patch.object(check.manage, 'ensure_gh', side_effect=download), \
                patch.object(check.subprocess, 'run') as run:
            check.main()
            self.assertEqual(os.environ['PATH'], '/original/bin')
            run.assert_called_once_with([str(destinations[0] / 'gh'), 'attestation', 'verify', '--help'],
                                        check=True, stdout=subprocess.DEVNULL)
            self.assertFalse(destinations[0].exists())
            with patch.object(check.manage, 'ensure_gh', side_effect=OSError('offline')):
                with self.assertRaises(OSError):
                    check.main()
            self.assertEqual(os.environ['PATH'], '/original/bin')

    def test_pinned_cli_archives_are_verified_before_binary_is_written(self):
        for system, asset_os, arch, suffix in (('Linux', 'linux', 'amd64', 'tar.gz'),
                                               ('Darwin', 'macOS', 'arm64', 'zip')):
            stem = f'gh_1.2.3_{asset_os}_{arch}'
            buffer = io.BytesIO()
            binary = b'#!/bin/sh\nexit 0\n'
            if suffix == 'zip':
                with zipfile.ZipFile(buffer, 'w') as archive:
                    archive.writestr(stem + '/bin/gh', binary)
            else:
                with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
                    member = tarfile.TarInfo(stem + '/bin/gh')
                    member.size = len(binary)
                    archive.addfile(member, io.BytesIO(binary))
            data = buffer.getvalue()
            pin = {'version': '1.2.3', 'sha256': {stem + '.' + suffix: hashlib.sha256(data).hexdigest()}}
            with self.subTest(system=system), tempfile.TemporaryDirectory() as directory, \
                    patch.object(manage, 'GH_PIN', pin), patch.object(manage.shutil, 'which', return_value='/outdated/gh'), \
                    patch.object(manage.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)), \
                    patch.object(manage.platform, 'system', return_value=system), \
                    patch.object(manage.platform, 'machine', return_value='arm64' if arch == 'arm64' else 'x86_64'), \
                    patch.dict(os.environ), patch.object(manage.urllib.request, 'urlopen', return_value=io.BytesIO(data)):
                destination = Path(directory)
                self.assertEqual(Path(manage.ensure_gh(destination)).read_bytes(), binary)
                self.assertEqual((destination / 'gh').stat().st_mode & 0o777, 0o755)
                (destination / 'gh').unlink()
                with patch.object(manage.urllib.request, 'urlopen', return_value=io.BytesIO(data + b'tampered')):
                    with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                        manage.ensure_gh(destination)
                self.assertFalse((destination / 'gh').exists())

    def test_capable_cli_is_reused_without_download(self):
        with patch.object(manage.shutil, 'which', return_value='/existing/gh'), \
                patch.object(manage.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), \
                patch.object(manage.urllib.request, 'urlopen') as download:
            self.assertEqual(manage.ensure_gh(), '/existing/gh')
            download.assert_not_called()

    def test_download_retries_only_transient_errors_with_backoff(self):
        for status, attempts in ((403, 1), (404, 1), (429, 4), (503, 4)):
            error = urllib.error.HTTPError('https://example.org', status, 'failure', {}, None)
            with self.subTest(status=status), patch.object(manage.urllib.request, 'urlopen', side_effect=error) as fetch, \
                    patch.object(manage.time, 'sleep') as sleep:
                with self.assertRaises(urllib.error.HTTPError):
                    manage.download('https://example.org')
                self.assertEqual(fetch.call_count, attempts)
                self.assertEqual([call.args[0] for call in sleep.call_args_list], [] if attempts == 1 else [1, 2, 4])

    def test_empty_or_invalid_access_settings_have_friendly_startup_errors(self):
        script = ROOT / 'build/runtime/start-envoy.py'
        for domain, audience, message in (('', 'a' * 64, 'CF_ACCESS_TEAM_DOMAIN'),
                                          ('https://team.cloudflareaccess.com', 'a' * 64, 'CF_ACCESS_TEAM_DOMAIN'),
                                          ('team.cloudflareaccess.com', '', 'CF_ACCESS_AUD')):
            result = subprocess.run(['python3', str(script)], capture_output=True, text=True,
                                    env={**os.environ, 'CF_ACCESS_TEAM_DOMAIN': domain, 'CF_ACCESS_AUD': audience})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Set ' + message, result.stderr)
            self.assertNotIn('Traceback', result.stderr)

    def test_restore_volume_preflight_errors_are_readable_and_archive_commas_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'backup,one.tar.gz'
            archive.touch()
            docker = root / 'docker'
            docker.write_text('#!/bin/sh\nexit 1\n')
            docker.chmod(0o755)
            result = subprocess.run(['sh', str(ROOT / 'build/tools/restore-home.sh'), str(archive), 'missing'],
                                    capture_output=True, text=True,
                                    env={**os.environ, 'PATH': str(root) + ':' + os.environ['PATH'],
                                         'BACKUP_GPG_PASSPHRASE': ''})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Cannot inspect restore volume', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
