"""Exercise installation without network access, packages, or containers."""
import gzip
import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('management', ROOT / 'manage.py')
management = importlib.util.module_from_spec(spec)
spec.loader.exec_module(management)
RELEASE_FILES = ['compose.yaml', 'compose.backup-common.yaml', 'compose.backup.yaml', 'compose.backup-local.yaml',
                 'manage', 'manage.py', '.env.example', 'build/tools/restore-home.sh', 'build/tools/backup-key.py']


def bundle(files):
    """Build a release bundle with stable bytes so its checksum can be computed separately."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return gzip.compress(buffer.getvalue(), mtime=0)


class InstallationTests(unittest.TestCase):
    def test_dotenv_helpers_reject_multiline_values(self):
        for quote in ("'", '"'):
            original = ("OTHER=" + quote + "first line\n"
                        "CODE_SERVER_IMAGE=ghcr.io/example/other:latest\n" + quote + "\n")
            for operation in (
                lambda: management.image_repository(original),
                lambda: management.merge_settings(original, "NEW=value\n", "v1.2.3"),
                lambda: management.merge_settings("", original, "v1.2.3"),
                lambda: management.select_image(original, "replacement"),
                lambda: management.replace_image_setting(original, "replacement"),
            ):
                with self.subTest(quote=quote, operation=operation):
                    with self.assertRaisesRegex(ValueError, "Multiline or unterminated"):
                        operation()

    def test_dotenv_validation_accepts_single_line_quotes_and_comments(self):
        management.validate_dotenv(
            "# OTHER='ignored\nSINGLE='literal' # comment\n"
            'DOUBLE="escaped \\" quote"\nEMPTY=""\nAPOSTROPHE=it\'s fine\n')

    def test_upgrade_repository_defaults_and_comments(self):
        for value in ('', "''", 'ghcr.io/example/fork:1.0.0\t# pinned', "'ghcr.io/example/fork:1.0.0' # pinned"):
            with self.subTest(value=value):
                expected = 'example/fork' if 'fork' in value else 'weeks-consulting/code-server-aio'
                self.assertEqual(management.image_repository('CODE_SERVER_IMAGE=' + value + '\n'), expected)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'deployment with spaces'
        self.args = management.parser().parse_args([
            'install', '--directory', str(self.directory), '--version', 'v1.2.3'])
        self.files = {Path(name).name: (ROOT / name).read_bytes() for name in RELEASE_FILES}
        self.checksums = lambda: (hashlib.sha256(bundle(self.files)).hexdigest() + '  ' + management.BUNDLE + '\n').encode()
        self.assets = [{'name': 'SHA256SUMS'}, {'name': management.BUNDLE}]
        self.release = {'tag_name': 'v1.2.3', 'assets': self.assets}
        self.downloads = []
        def fetch(url):
            self.downloads.append(url)
            if url.endswith('/SHA256SUMS'):
                return self.checksums()
            if url.endswith('/releases/latest') or '/releases/tags/' in url:
                return json.dumps(self.release).encode()
            if url.endswith('/' + management.BUNDLE):
                return bundle(self.files)
            raise AssertionError('unexpected download: ' + url)
        self.fetch = fetch
        for mock in (patch.object(management, 'dependencies'),
                     patch.object(management.os, 'geteuid', return_value=1000),
                     patch.object(management, 'download', side_effect=fetch)):
            mock.start()
            self.addCleanup(mock.stop)

    def test_zero_revision_keeps_bundle_and_image_on_selected_release(self):
        self.args.version = 'v4.144.0-0'
        management.install(self.args)
        self.assertEqual(self.downloads, [
            'https://github.com/Weeks-Consulting/code-server-aio/releases/download/v4.144.0-0/' + management.BUNDLE,
            'https://github.com/Weeks-Consulting/code-server-aio/releases/download/v4.144.0-0/SHA256SUMS',
        ])
        self.assertIn('CODE_SERVER_IMAGE=ghcr.io/weeks-consulting/code-server-aio:4.144.0-0',
                      (self.directory / '.env').read_text())
        self.assertEqual(management.stable_tag({'tag_name': 'v4.144.0-0'}), 'v4.144.0-0')

    def test_install_and_refresh_preserve_settings_and_backup_files(self):
        management.install(self.args)
        self.assertEqual((self.directory / 'manage.py').read_bytes(), self.files['manage.py'])
        self.assertEqual((self.directory / 'manage').stat().st_mode & 0o777, 0o755)
        env = self.directory / '.env'
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        self.assertIn('CODE_SERVER_IMAGE=ghcr.io/weeks-consulting/code-server-aio:1.2.3', env.read_text())
        self.assertRegex(env.read_text(), r'BACKUP_GPG_PASSPHRASE=[a-f0-9]{64}')
        digest = 'ghcr.io/example/image@sha256:' + 'a' * 64
        env.write_text('# My settings\nCODE_SERVER_IMAGE=' + digest + '\nSECRET=$(never-execute)\nBACKUP_GPG_PASSPHRASE=existing\n')
        management.install(self.args)
        self.assertIn('CODE_SERVER_IMAGE=' + digest, env.read_text())
        self.assertIn('SECRET=$(never-execute)', env.read_text())
        self.assertIn('BACKUP_GPG_PASSPHRASE=existing', env.read_text())
        self.assertIn('CF_TUNNEL_TOKEN=', env.read_text())
        self.assertEqual((self.directory / 'manage.py.bak').read_bytes(), self.files['manage.py'])

    def test_checksum_failure_leaves_deployment_untouched(self):
        self.directory.mkdir()
        env = self.directory / '.env'
        env.write_text('SECRET=original\n')
        self.checksums = lambda: b'wrong  compose.yaml\n'
        with self.assertRaisesRegex(ValueError, 'checksum'):
            management.install(self.args)
        self.assertEqual(env.read_text(), 'SECRET=original\n')
        self.assertEqual(list(self.directory.iterdir()), [env])

    def test_failed_refresh_restores_deployment_and_encryption_key(self):
        management.install(self.args)
        names = [*management.DEPLOYMENT_FILES, '.env']
        before = {name: (self.directory / name).read_bytes() for name in names}
        replace = management.os.replace
        def fail_env(source, destination):
            if Path(source).name.startswith('new-') and Path(destination).name == '.env':
                raise OSError('simulated replacement failure')
            return replace(source, destination)
        with patch.object(management.os, 'replace', side_effect=fail_env):
            with self.assertRaisesRegex(OSError, 'simulated replacement'):
                management.install(self.args)
        self.assertEqual(before, {name: (self.directory / name).read_bytes() for name in names})
        self.assertFalse(list(self.directory.glob('.install-*')))

    def test_failed_staging_leaves_existing_configuration_untouched(self):
        management.install(self.args)
        before = (self.directory / '.env').read_bytes()
        write = Path.write_bytes
        def fail_candidate(path, data):
            if path.name.startswith('new-'):
                raise OSError('simulated disk full')
            return write(path, data)
        with patch.object(Path, 'write_bytes', fail_candidate):
            with self.assertRaisesRegex(OSError, 'disk full'):
                management.install(self.args)
        self.assertEqual((self.directory / '.env').read_bytes(), before)
        self.assertFalse(list(self.directory.glob('.install-*')))

    def test_missing_required_release_files_preserves_existing_deployment(self):
        self.directory.mkdir()
        env = self.directory / '.env'
        env.write_text('SECRET=original\n')
        for missing in (management.BUNDLE, 'SHA256SUMS'):
            with self.subTest(missing=missing):
                error = urllib.error.HTTPError('https://example.org/' + missing, 404, 'missing', {}, None)
                self.addCleanup(error.close)
                def fetch(url):
                    if url.endswith('/' + missing):
                        raise error
                    return self.fetch(url)
                with patch.object(management, 'download', side_effect=fetch):
                    with self.assertRaisesRegex(ValueError, 'lacks required deployment files'):
                        management.install(self.args)
                self.assertEqual(env.read_text(), 'SECRET=original\n')
                self.assertEqual(list(self.directory.iterdir()), [env])
        files = self.files
        for missing in ('compose.backup-common.yaml', 'manage.py', 'restore-home.sh', '.env.example'):
            with self.subTest(member=missing):
                self.files = {name: data for name, data in files.items() if name != missing}
                with self.assertRaisesRegex(ValueError, 'bundle lacks required deployment files: ' + re.escape(missing)):
                    management.install(self.args)
                self.assertEqual(list(self.directory.iterdir()), [env])
        self.files = files

    def test_unfinished_latest_release_is_reported_as_publishing(self):
        self.args.version = None
        def fetch(url):
            if url.endswith('/releases/latest'):
                return json.dumps({'tag_name': 'v1.2.3', 'assets': []}).encode()
            raise AssertionError('downloaded before publication check: ' + url)
        with patch.object(management, 'download', side_effect=fetch):
            with self.assertRaisesRegex(ValueError, 'Release v1.2.3 is still publishing'):
                management.install(self.args)
        self.assertFalse(self.directory.exists())

    def test_default_directory_depends_on_user(self):
        self.args.directory = None
        self.args.dry_run = True
        home = Path(self.temp.name) / 'home'
        for uid, expected in ((1000, home / 'code-server'), (0, Path('/opt/code-server'))):
            with self.subTest(uid=uid), patch.dict(os.environ, HOME=str(home)), \
                    patch.object(management.os, 'geteuid', return_value=uid), \
                    patch.object(management, 'validate_destination'), \
                    patch('builtins.print') as output:
                management.install(self.args)
            self.assertIn(f'{expected}/compose.yaml', str(output.call_args_list))

    def test_dry_run_has_no_downloads_or_filesystem_changes(self):
        self.args.dry_run = True
        with patch.object(management, 'download') as download:
            management.install(self.args)
        download.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_validation_precedes_dependencies_and_downloads(self):
        for attr, value in [('version', 'not-a-tag'), ('repository', '../unsafe')]:
            with self.subTest(attr=attr), patch.object(management, 'dependencies') as dependencies:
                original = getattr(self.args, attr)
                setattr(self.args, attr, value)
                with self.assertRaises(ValueError):
                    management.install(self.args)
                dependencies.assert_not_called()
                setattr(self.args, attr, original)
        self.directory.mkdir()
        (self.directory / 'manage.py').symlink_to(ROOT / 'manage.py')
        with self.assertRaisesRegex(ValueError, 'regular file'):
            management.install(self.args)

    def test_root_rejects_writable_ancestors(self):
        # act may run as root with TMPDIR on a root-owned Docker volume.
        # Create the unsafe ancestor instead of relying on host temp permissions.
        self.directory.parent.chmod(0o777)
        with patch.object(management.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(ValueError, 'Root installation requires'):
                management.validate_destination(self.directory, [])

    def test_default_image_remains_latest(self):
        self.args.version = None
        management.install(self.args)
        self.assertIn('CODE_SERVER_IMAGE=ghcr.io/weeks-consulting/code-server-aio:latest',
                      (self.directory / '.env').read_text())

    def install_release(self, image):
        """Install v1.2.3, leave its compose.yaml stale, then publish v1.3.0."""
        management.install(self.args)
        env = self.directory / '.env'
        env.write_text(env.read_text().replace(
            'CODE_SERVER_IMAGE=ghcr.io/weeks-consulting/code-server-aio:1.2.3', 'CODE_SERVER_IMAGE=' + image)
            + 'SECRET=$(never-execute)\n')
        (self.directory / 'compose.yaml').write_text('# stale release\n')
        self.release = {'tag_name': 'v1.3.0', 'assets': self.assets,
                        'html_url': 'https://github.com/Example/Fork/releases/tag/v1.3.0'}
        self.downloads.clear()
        return env

    def deployment_state(self):
        return {path.name: path.read_bytes() for path in self.directory.iterdir() if path.is_file()}

    def test_upgrade_refreshes_files_and_replaces_digest_pin(self):
        env = self.install_release('ghcr.io/example/fork@sha256:' + 'a' * 64)
        key = next(line for line in env.read_text().splitlines() if line.startswith('BACKUP_GPG_PASSPHRASE='))
        management.upgrade(self.directory)
        text = env.read_text()
        self.assertIn('CODE_SERVER_IMAGE=ghcr.io/example/fork:1.3.0\n', text)
        self.assertNotIn('@sha256:', text)
        self.assertIn('SECRET=$(never-execute)', text)
        self.assertIn(key, text)
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.directory / 'compose.yaml').read_bytes(), self.files['compose.yaml'])
        self.assertEqual((self.directory / 'compose.yaml.bak').read_text(), '# stale release\n')
        # GHCR's lowercase name maps back to GitHub's spelling for release downloads.
        self.assertEqual(self.downloads[0], 'https://api.github.com/repos/example/fork/releases/latest')
        self.assertEqual(self.downloads[1:], [
            'https://github.com/Example/Fork/releases/download/v1.3.0/' + management.BUNDLE,
            'https://github.com/Example/Fork/releases/download/v1.3.0/SHA256SUMS'])

    def test_upgrade_selects_prerelease_and_checks_bundle(self):
        env = self.install_release('ghcr.io/example/fork:1.2.3')
        self.release['prerelease'] = True
        args = management.parser().parse_args(['upgrade', '--version', 'v1.3.0'])
        management.validate_args(args)
        with patch.object(management.os, 'chdir'):
            management.manage(args, self.directory)
        self.assertEqual(self.downloads[0], 'https://api.github.com/repos/example/fork/releases/tags/v1.3.0')
        self.assertIn('CODE_SERVER_IMAGE=ghcr.io/example/fork:1.3.0\n', env.read_text())
        self.assertEqual((self.directory / 'compose.yaml').read_bytes(), self.files['compose.yaml'])

    def test_selected_upgrade_rejects_bad_release_without_changes(self):
        self.install_release('ghcr.io/example/fork:1.2.3')
        before = self.deployment_state()
        for version, release in (
            ('v1.3.0', {**self.release, 'draft': True}),
            ('v1.4.0', self.release),
            ('v1.3.0', {**self.release, 'assets': []}),
            ('v1.3.0', {**self.release, 'html_url': 'https://github.com/other/repo/releases/tag/v1.3.0'}),
            ('../latest', self.release),
        ):
            with self.subTest(version=version, release=release), patch.dict(self.release, release, clear=True):
                with self.assertRaises(SystemExit):
                    management.upgrade(self.directory, version)
                self.assertEqual(before, self.deployment_state())

    def test_upgrade_failures_leave_deployment_unchanged(self):
        self.install_release('ghcr.io/example/fork:1.2.3')
        before = self.deployment_state()
        def offline(url):
            raise OSError('offline')
        cases = (
            ('offline', {'download': offline}),
            ('invalid tag', {'release': {'tag_name': 'invalid', 'assets': self.assets}}),
            ('publishing', {'release': {'tag_name': 'v1.3.0', 'assets': [{'name': 'SHA256SUMS'}]}}),
            ('other repository', {'release': {**self.release,
                                              'html_url': 'https://github.com/other/repo/releases/tag/v1.3.0'}}),
            ('checksum mismatch', {'checksums': b'0' * 64 + b'  ' + management.BUNDLE.encode() + b'\n'}),
            ('custom registry', {'image': 'example/custom:fixed'}),
        )
        for label, case in cases:
            env = self.directory / '.env'
            original = env.read_text()
            checksums = self.checksums
            with self.subTest(label), \
                    patch.object(management, 'download', side_effect=case.get('download', self.fetch)), \
                    patch.dict(self.release, case.get('release', {}), clear='release' in case):
                if 'checksums' in case:
                    self.checksums = lambda: case['checksums']
                if 'image' in case:
                    env.write_text(original.replace('ghcr.io/example/fork:1.2.3', case['image']))
                try:
                    with self.assertRaises(SystemExit) as raised:
                        management.upgrade(self.directory)
                    self.assertIn('Unable to upgrade', str(raised.exception.code))
                finally:
                    self.checksums = checksums
                    if 'image' in case:
                        self.assertIn(case['image'], env.read_text())
                        env.write_text(original)
                self.assertEqual(before, self.deployment_state())
                self.assertFalse(list(self.directory.glob('.install-*')))

    def test_upgrade_refuses_to_overwrite_a_source_checkout(self):
        self.install_release('ghcr.io/example/fork:1.2.3')
        (self.directory / 'build/tools').mkdir(parents=True)
        (self.directory / 'build/tools/install.sh').write_text('# checkout\n')
        before = {path: path.read_bytes() for path in self.directory.rglob('*') if path.is_file()}
        with self.assertRaises(SystemExit) as raised:
            management.upgrade(self.directory)
        self.assertIn('source checkout', str(raised.exception.code))
        self.assertEqual(before, {path: path.read_bytes() for path in self.directory.rglob('*') if path.is_file()})
        self.assertEqual(self.downloads, [])

    def test_upgrade_waits_for_the_shared_lock_and_preserves_concurrent_changes(self):
        env = self.install_release('ghcr.io/example/fork:1.2.3')
        errors = []
        def run():
            try:
                management.upgrade(self.directory)
            except BaseException as error:
                errors.append(error)
        with (self.directory / '.env.lock').open('w') as lock:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
            worker = threading.Thread(target=run)
            worker.start()
            try:
                worker.join(timeout=0.3)
                self.assertTrue(worker.is_alive())
                env.write_text(env.read_text() + 'CONCURRENT=kept\n')
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                worker.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertIn('CONCURRENT=kept', env.read_text())
        self.assertIn('CODE_SERVER_IMAGE=ghcr.io/example/fork:1.3.0', env.read_text())

    @unittest.skipUnless(os.geteuid() == 0, 'requires root to model sudo ownership')
    def test_upgrade_preserves_another_users_env_and_lock(self):
        env = self.install_release('ghcr.io/example/fork:1.2.3')
        os.chown(env, 65534, 65534)
        os.chown(self.directory / '.env.lock', 65534, 65534)
        # The temp directory's world-writable ancestors fail root destination checks,
        # which test_root_rejects_writable_ancestors covers; this test is about ownership.
        with patch.object(management.os, 'geteuid', return_value=0), \
                patch.object(management, 'validate_destination'):
            management.upgrade(self.directory)
        for path in (env, self.directory / '.env.lock'):
            self.assertEqual((path.stat().st_uid, path.stat().st_gid), (65534, 65534))


class DownloadTests(unittest.TestCase):
    def test_truncated_responses_are_retried(self):
        class Response:
            def __init__(self, outcome):
                self.outcome = outcome
            def __enter__(self):
                return self
            def __exit__(self, *exc):
                return False
            def read(self):
                if isinstance(self.outcome, Exception):
                    raise self.outcome
                return self.outcome
        outcomes = [http.client.IncompleteRead(b'partial'), b'complete']
        with patch.object(management.urllib.request, 'urlopen',
                          side_effect=lambda *a, **k: Response(outcomes.pop(0))), \
                patch.object(management.time, 'sleep') as sleep:
            self.assertEqual(management.download('https://example.invalid/file'), b'complete')
        sleep.assert_called_once_with(1)


class DependencyTests(unittest.TestCase):
    def test_missing_compose_fails_without_installing_packages(self):
        with patch.object(management.shutil, 'which', side_effect=lambda name: name), \
                patch.object(management.subprocess, 'run',
                             return_value=subprocess.CompletedProcess([], 1)) as commands:
            with self.assertRaisesRegex(ValueError, 'Docker Compose is required'):
                management.dependencies()
        self.assertEqual([call.args[0] for call in commands.call_args_list], [['docker', 'compose', 'version']])

    def test_missing_docker_fails(self):
        with patch.object(management.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'working Docker installation'):
                management.dependencies()


class BootstrapTests(unittest.TestCase):
    def test_checksum_precedes_execution_and_needs_no_github_cli(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / 'bootstrap/tools/install.sh'
            script.parent.mkdir(parents=True)
            script.write_bytes((ROOT / 'build/tools/install.sh').read_bytes())
            tools = root / 'bin'
            tools.mkdir()
            payload = (f'import sys\nfrom pathlib import Path\nPath({str(root / "executed")!r}).write_text(" ".join(sys.argv[1:]))\n').encode()
            manifest = hashlib.sha256(payload).hexdigest() + '  manage.py\n'
            curl = tools / 'curl'
            curl.write_text(
                f'#!{sys.executable}\nimport json, os, pathlib, sys\n'
                'args = sys.argv[1:]\n'
                'url = args[args.index("--output") - 1]\n'
                'repo = os.environ.get("EXPECTED_REPOSITORY", "Weeks-Consulting/code-server-aio")\n'
                'tag = os.environ.get("EXPECTED_VERSION", "v4.144.0-0")\n'
                'base = f"https://github.com/{repo}/releases/download/{tag}/"\n'
                'if os.environ.get("PUBLISHING"): sys.exit(22)\n'
                'if url == f"https://api.github.com/repos/{repo}/releases/latest":\n'
                '    pathlib.Path(args[args.index("--output") + 1]).write_text(json.dumps({"tag_name": "v4.144.0-0"}))\n'
                '    sys.exit(0)\n'
                'if not url.startswith(base): sys.exit(22)\n'
                f'payload = {payload!r}\nmanifest = {manifest!r}\n'
                'data = manifest.encode() if url.endswith("/SHA256SUMS") else payload + (b"# tampered" if os.environ.get("TAMPER") else b"")\n'
                'pathlib.Path(args[args.index("--output") + 1]).write_bytes(data)\n')
            # The bootstrap must never need the GitHub CLI or a GitHub login.
            gh = tools / 'gh'
            gh.write_text(f'#!/bin/sh\ntouch {str(root / "gh-called")!r}\nexit 99\n')
            curl.chmod(0o755)
            gh.chmod(0o755)
            caller = root / 'caller' / 'nested'
            caller.mkdir(parents=True)
            (root / 'manage.py').write_text('raise RuntimeError("unverified local file executed")')
            env = {**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH']}
            env.pop('GH_TOKEN', None)
            env.pop('GITHUB_TOKEN', None)
            for overrides, succeeds in (({'TAMPER': '1'}, False), ({}, True)):
                result = subprocess.run(['sh', '-s', '--'], input=script.read_text(), cwd=caller, env={**env, **overrides}, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                self.assertEqual((root / 'executed').exists(), succeeds)
            # Both version spellings and fork selection control every asset URL.
            for options, overrides, expected in (
                (['--version', 'v4.144.0-0'], {}, 'install --version v4.144.0-0 --version v4.144.0-0'),
                (['--version=v1.2.0', '--repository=Example/Fork'],
                 {'EXPECTED_VERSION': 'v1.2.0', 'EXPECTED_REPOSITORY': 'Example/Fork'},
                 'install --version=v1.2.0 --repository=Example/Fork --version v1.2.0'),
            ):
                (root / 'executed').unlink()
                result = subprocess.run(['sh', '-s', '--', *options], input=script.read_text(),
                                        cwd=caller, env={**env, **overrides}, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / 'executed').read_text(), expected)
            (root / 'executed').unlink()
            result = subprocess.run(['sh', '-s', '--'], input=script.read_text(), cwd=caller,
                                    env={**env, 'PUBLISHING': '1'}, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('retry after it finishes', result.stderr)
            self.assertFalse((root / 'executed').exists())
            self.assertFalse((root / 'gh-called').exists())


if __name__ == '__main__':
    unittest.main()
