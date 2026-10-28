"""Exercise installation without network access, packages, or containers."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('management', ROOT / 'manage.py')
management = importlib.util.module_from_spec(spec)
spec.loader.exec_module(management)


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

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'deployment with spaces'
        self.args = management.parser().parse_args([
            'install', '--directory', str(self.directory), '--version', 'v1.2.3'])
        self.files = {name: (ROOT / name).read_bytes() for name in
                      ['compose.yaml', 'compose.backup-common.yaml', 'compose.backup.yaml', 'compose.backup-local.yaml',
                       'manage', 'manage.py', '.env.example',
                       'build/tools/restore-home.sh', 'build/tools/backup-key.py']}
        self.checksums = lambda: ''.join(
            hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
            for name, data in self.files.items()).encode()
        def fetch(url):
            if url.endswith('/SHA256SUMS'):
                return self.checksums()
            if url.endswith('/releases/latest'):
                return json.dumps({'tag_name': 'v1.2.3'}).encode()
            return self.files[url.split('/refs/tags/v1.2.3/')[1]]
        self.fetch = fetch
        for mock in (patch.object(management, 'require_github_auth'),
                     patch.object(management, 'dependencies'),
                     patch.object(management, 'verify_release'),
                     patch.object(management.os, 'geteuid', return_value=1000),
                     patch.object(management, 'ensure_gh', return_value='gh'),
                     patch.object(management, 'download', side_effect=fetch)):
            mock.start()
            self.addCleanup(mock.stop)

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

    def test_attestation_failure_leaves_deployment_untouched(self):
        self.directory.mkdir()
        env = self.directory / '.env'
        env.write_text('BACKUP_GPG_PASSPHRASE=original\n')
        with patch.object(management, 'verify_release', side_effect=ValueError('untrusted release')):
            with self.assertRaisesRegex(ValueError, 'untrusted release'):
                management.install(self.args)
        self.assertEqual(env.read_text(), 'BACKUP_GPG_PASSPHRASE=original\n')
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
        for missing in ('compose.backup-common.yaml', 'manage.py', 'restore-home.sh', 'SHA256SUMS'):
            with self.subTest(missing=missing):
                def fetch(url):
                    if url.endswith('/' + missing):
                        raise urllib.error.HTTPError(url, 404, 'missing', {}, None)
                    return self.fetch(url)
                with patch.object(management, 'download', side_effect=fetch):
                    with self.assertRaisesRegex(ValueError, 'lacks required deployment files'):
                        management.install(self.args)
                self.assertEqual(env.read_text(), 'SECRET=original\n')
                self.assertEqual(list(self.directory.iterdir()), [env])

    def test_dry_run_has_no_downloads_or_filesystem_changes(self):
        self.args.dry_run = True
        with patch.object(management, 'download') as download, \
                patch.object(management, 'require_github_auth') as auth:
            management.install(self.args)
        auth.assert_not_called()
        download.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_authentication_precedes_dependencies_and_downloads(self):
        with patch.object(management, 'require_github_auth', side_effect=ValueError('missing credentials')), \
                patch.object(management, 'dependencies') as dependencies, \
                patch.object(management, 'download') as download:
            with self.assertRaisesRegex(ValueError, 'missing credentials'):
                management.install(self.args)
            dependencies.assert_not_called()
            download.assert_not_called()

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


class AuthenticationTests(unittest.TestCase):
    def test_missing_credentials_fail_without_downloads(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(management.shutil, 'which', return_value=None), \
                patch.object(management, 'download') as download:
            with self.assertRaisesRegex(ValueError, 'sudo --preserve-env=GH_TOKEN'):
                management.require_github_auth()
            download.assert_not_called()

    def test_environment_tokens_need_no_cli(self):
        for name in ('GH_TOKEN', 'GITHUB_TOKEN'):
            with self.subTest(name=name), patch.dict(os.environ, {name: 'test-token'}, clear=True), \
                    patch.object(management.shutil, 'which') as which:
                management.require_github_auth()
                which.assert_not_called()

    def test_existing_cli_login_is_checked_for_github_com(self):
        for status in (0, 1):
            with self.subTest(status=status), patch.dict(os.environ, {}, clear=True), \
                    patch.object(management.shutil, 'which', return_value='/bin/gh'), \
                    patch.object(management.subprocess, 'run',
                                 return_value=subprocess.CompletedProcess([], status)) as run:
                if status:
                    with self.assertRaisesRegex(ValueError, 'GitHub authentication'):
                        management.require_github_auth()
                else:
                    management.require_github_auth()
                self.assertEqual(run.call_args.args[0],
                                 ['/bin/gh', 'auth', 'status', '--hostname', 'github.com'])


class DependencyTests(unittest.TestCase):
    def test_debian_dry_run_reports_packages_without_installing(self):
        import contextlib
        import io
        import subprocess
        def run(command, **kwargs):
            if command[:2] == ['dpkg-query', '-W']:
                return subprocess.CompletedProcess(command, 0, 'install ok installed', '')
            return subprocess.CompletedProcess(command, 1)
        output = io.StringIO()
        with patch.object(management.platform, 'system', return_value='Linux'), \
                patch.object(management.Path, 'is_file', return_value=True), \
                patch.object(management.shutil, 'which', side_effect=lambda name: None if name == 'gpg' else name), \
                patch.object(management.os, 'geteuid', return_value=1000), \
                patch.object(management.subprocess, 'run', side_effect=run) as commands, \
                contextlib.redirect_stdout(output):
            management.dependencies(True)
        self.assertIn('Would run: sudo apt-get update', output.getvalue())
        self.assertIn('sudo apt-get install -y gnupg docker-compose-plugin', output.getvalue())
        self.assertFalse(any('apt-get' in call.args[0] for call in commands.call_args_list))

    def test_macos_registers_homebrew_compose_plugin(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(management.os.environ, DOCKER_CONFIG=directory), \
                    patch.object(management.platform, 'system', return_value='Darwin'), \
                    patch.object(management.shutil, 'which', side_effect=lambda name: name), \
                    patch.object(management.os, 'geteuid', return_value=1000), \
                    patch.object(management.subprocess, 'run', side_effect=[
                        subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0),
                        subprocess.CompletedProcess([], 1), subprocess.CompletedProcess([], 0)]) as commands, \
                    patch.object(management.subprocess, 'check_output', return_value='/opt/homebrew\n'):
                with patch.object(management, 'ensure_gh', return_value='gh'):
                    management.dependencies(False)
            target = Path(directory) / 'cli-plugins/docker-compose'
            self.assertTrue(target.is_symlink())
            self.assertEqual(str(target.readlink()), '/opt/homebrew/lib/docker/cli-plugins/docker-compose')
            self.assertIn(['brew', 'install', 'docker-compose'], [call.args[0] for call in commands.call_args_list])


class BootstrapTests(unittest.TestCase):
    def test_missing_authentication_stops_before_bootstrap_downloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            tools = Path(temporary)
            for name in ('gh', 'curl'):
                tool = tools / name
                tool.write_text('#!/bin/sh\nexit 99\n')
                tool.chmod(0o755)
            env = {**os.environ, 'PATH': str(tools) + ':' + os.environ['PATH']}
            env.pop('GH_TOKEN', None)
            env.pop('GITHUB_TOKEN', None)
            result = subprocess.run(['sh', '-s', '--'],
                                    input=(ROOT / 'build/tools/install.sh').read_text(),
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('GitHub authentication is required', result.stderr)
            self.assertIn('sudo --preserve-env=GH_TOKEN', result.stderr)

    def test_verification_precedes_execution_and_failures_stop_bootstrap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / 'bootstrap/tools/install.sh'
            script.parent.mkdir(parents=True)
            script.write_bytes((ROOT / 'build/tools/install.sh').read_bytes())
            tools = root / 'bin'
            tools.mkdir()
            payload = (f'from pathlib import Path\nPath({str(root / "executed")!r}).touch()\n').encode()
            manifest = hashlib.sha256(payload).hexdigest() + '  manage.py\n'
            curl = tools / 'curl'
            curl.write_text(
                f'#!{sys.executable}\nimport os, pathlib, sys\n'
                'args = sys.argv[1:]\n'
                'url = args[args.index("--output") - 1]\n'
                f'payload = {payload!r}\nmanifest = {manifest!r}\n'
                'data = b\'{"tag_name":"v1.2.3"}\' if url.endswith("/releases/latest") else '
                'manifest.encode() if url.endswith("/SHA256SUMS") else payload + (b"# tampered" if os.environ.get("TAMPER") else b"")\n'
                'pathlib.Path(args[args.index("--output") + 1]).write_bytes(data)\n')
            gh = tools / 'gh'
            gh.write_text(f'#!{sys.executable}\nimport os, pathlib, sys\n'
                          'assert sys.argv[1:3] == ["attestation", "verify"]\n'
                          'if "--help" in sys.argv: sys.exit(0)\n'
                          'assert "--signer-workflow" in sys.argv\n'
                          f'assert not pathlib.Path({str(root / "executed")!r}).exists()\n'
                          'sys.exit(1 if os.environ.get("REJECT") else 0)\n')
            curl.chmod(0o755)
            gh.chmod(0o755)
            caller = root / 'caller' / 'nested'
            caller.mkdir(parents=True)
            (root / 'manage.py').write_text('raise RuntimeError("unverified local file executed")')
            env = {**os.environ, 'GH_TOKEN': 'test-token', 'PATH': str(tools) + ':' + os.environ['PATH']}
            for overrides, succeeds in (({'REJECT': '1'}, False), ({'TAMPER': '1'}, False), ({}, True)):
                result = subprocess.run(['sh', '-s', '--'], input=script.read_text(), cwd=caller, env={**env, **overrides}, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                self.assertEqual((root / 'executed').exists(), succeeds)


if __name__ == '__main__':
    unittest.main()
