"""Exercise installation without network access, packages, or containers."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('management', ROOT / 'manage.py')
management = importlib.util.module_from_spec(spec)
spec.loader.exec_module(management)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'deployment with spaces'
        self.args = management.parser().parse_args([
            'install', '--directory', str(self.directory), '--version', 'v1.2.3'])
        self.files = {name: (ROOT / name).read_bytes() for name in
                      ['compose.yaml', 'compose.backup.yaml', 'compose.backup-local.yaml',
                       'manage', 'manage.py', '.env.example',
                       'build/tools/restore-home.sh', 'build/tools/backup-key.py']}
        self.checksums = lambda: ''.join(
            hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
            for name, data in self.files.items()).encode()
        def fetch(url, optional=False):
            if url.endswith('/SHA256SUMS'):
                return self.checksums()
            if url.endswith('/releases/latest'):
                return json.dumps({'tag_name': 'v1.2.3'}).encode()
            return self.files[url.split('/refs/tags/v1.2.3/')[1]]
        self.fetch = fetch
        for mock in (patch.object(management, 'dependencies'),
                     patch.object(management.os, 'geteuid', return_value=1000),
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
        with patch.object(management.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(ValueError, 'Root installation requires'):
                management.validate_destination(self.directory, [])

    def test_default_image_remains_latest(self):
        self.args.version = None
        management.install(self.args)
        self.assertIn('CODE_SERVER_IMAGE=ghcr.io/weeks-consulting/code-server-aio:latest',
                      (self.directory / '.env').read_text())


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
                management.dependencies(False)
            target = Path(directory) / 'cli-plugins/docker-compose'
            self.assertTrue(target.is_symlink())
            self.assertEqual(str(target.readlink()), '/opt/homebrew/lib/docker/cli-plugins/docker-compose')
            self.assertIn(['brew', 'install', 'docker-compose'], [call.args[0] for call in commands.call_args_list])


if __name__ == '__main__':
    unittest.main()
