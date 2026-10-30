"""Regression coverage for downloads, startup diagnostics, and restore preflight."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('manage', ROOT / 'manage.py')
manage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manage)


class ReviewFixTests(unittest.TestCase):
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
