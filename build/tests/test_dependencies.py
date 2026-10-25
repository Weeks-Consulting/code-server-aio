"""Exercise upgrades without network access or changing checkout pins."""
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('dependencies', ROOT / 'build/tools/dependencies.py')
deps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deps)


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('build', '.github'):
            shutil.copytree(ROOT / name, self.root / name)
        for name in deps.COMPOSE:
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / name, self.root / name)

    def upstream(self, repo, endpoint):
        if endpoint == 'commits/HEAD' or endpoint.startswith('commits/'):
            return {'sha': 'a' * 40}
        version = '99.0.0'
        names = ([f'code-server_{version}_{arch}.deb' for arch in ('amd64', 'arm64')]
                 if repo == 'coder/code-server' else
                 [f'act_{system}_{arch}.tar.gz' for system in ('Linux', 'Darwin')
                  for arch in ('arm64', 'x86_64')])
        return {'tag_name': 'v' + version,
                'assets': [{'name': name, 'digest': 'sha256:' + 'b' * 64} for name in names]}

    def test_plan_updates_all_architectures_and_duplicate_pins(self):
        with patch.object(deps, 'github', side_effect=self.upstream), \
                patch.object(deps, 'fetch', return_value=b'installer'), \
                patch.object(deps, 'image_digest', return_value='c' * 64):
            before = deps.inventory(self.root)
            _, latest, edits = deps.plan(self.root)
        self.assertEqual(deps.inventory(self.root), before)
        self.assertEqual(latest['act'], '99.0.0')
        self.assertEqual(len(edits['build/tests/act.sha256'].splitlines()), 4)
        self.assertEqual(len(edits['build/code-server.sha256'].splitlines()), 2)
        self.assertIn(hashlib.sha256(b'installer').hexdigest(), edits['build/homebrew-install.sha256'])
        for name in ('compose.backup.yaml', 'compose.backup-local.yaml'):
            self.assertIn('sha256:' + 'c' * 64, edits[name])
        self.assertNotIn('# v6\n', edits[deps.WORKFLOW])
        self.assertIn('catthehacker/ubuntu', latest)
        self.assertEqual(edits['build/tests/actions.sh'].count('@sha256:' + 'c' * 64), 2)

    def test_confirmation_and_eof(self):
        target = self.root / 'build/tests/act.version'
        original = target.read_text()
        for answer in ('n', '', EOFError(), 'yes'):
            with self.subTest(answer=answer), patch.object(deps, 'ROOT', self.root), \
                    patch('sys.argv', ['dependencies.py', 'upgrade']), \
                    patch.object(deps, 'plan', return_value=({}, {}, {'build/tests/act.version': '99.0.0\n'})), \
                    patch('builtins.input', side_effect=answer if isinstance(answer, Exception) else None,
                          return_value=answer), contextlib.redirect_stdout(io.StringIO()):
                deps.main()
                self.assertEqual(target.read_text(), '99.0.0\n' if answer == 'yes' else original)

    def test_lookup_failure_leaves_pins_unchanged(self):
        before = deps.inventory(self.root)
        with patch.object(deps, 'github', side_effect=self.upstream), \
                patch.object(deps, 'fetch', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                deps.plan(self.root)
        self.assertEqual(deps.inventory(self.root), before)

    def test_outdated_never_prompts_or_writes(self):
        before = deps.inventory(self.root)
        output = io.StringIO()
        with patch.object(deps, 'ROOT', self.root), \
                patch('sys.argv', ['dependencies.py', 'outdated']), \
                patch.object(deps, 'github', side_effect=self.upstream), \
                patch.object(deps, 'image_digest', return_value='c' * 64), \
                patch.object(deps, 'fetch', side_effect=AssertionError('unexpected asset download')), \
                patch('builtins.input', side_effect=AssertionError('unexpected prompt')), \
                contextlib.redirect_stdout(output):
            deps.main()
        self.assertEqual(deps.inventory(self.root), before)
        self.assertIn('Outdated repository pins found', output.getvalue())
        self.assertIn('latest: 99.0.0', output.getvalue())

    def test_outdated_shows_only_changed_pins(self):
        current = {'current-package': '1.0.0', 'old-package': '1.0.0',
                   'image': 'latest @ sha256:' + 'a' * 64}
        for hashes in (False, True):
            for changed in (False, True):
                with self.subTest(hashes=hashes, changed=changed):
                    latest = dict(current)
                    if changed:
                        latest['old-package'] = '2.0.0'
                        latest['image'] = 'latest @ sha256:' + 'b' * 64
                    output = io.StringIO()
                    with patch('sys.argv', ['dependencies.py', 'outdated'] + (['--hashes'] if hashes else [])), \
                            patch.object(deps, 'plan', return_value=(current, latest, {'pin': 'new'} if changed else {})), \
                            contextlib.redirect_stdout(output):
                        deps.main()
                    text = output.getvalue()
                    self.assertNotIn('current-package:', text)
                    self.assertNotIn('(current)', text)
                    if changed:
                        self.assertIn('old-package: 1.0.0', text)
                        self.assertIn('latest: 2.0.0', text)
                        self.assertIn('image: latest', text)
                    else:
                        self.assertIn('All repository pins are current.', text)
                        self.assertNotIn('old-package:', text)
                        self.assertNotIn('image:', text)

    def test_version_hides_all_hashes_and_needs_no_network(self):
        output = io.StringIO()
        with patch.object(deps, 'ROOT', self.root), \
                patch('sys.argv', ['dependencies.py', 'version']), \
                patch.object(deps, 'fetch', side_effect=AssertionError('unexpected network')), \
                contextlib.redirect_stdout(output):
            deps.main()
        text = output.getvalue()
        self.assertIn('code-server:', text)
        self.assertIn('actions/checkout: v', text)
        self.assertNotRegex(text, r'[a-f0-9]{40}')
        self.assertNotIn('sha256:', text)

    def test_hash_option_for_all_commands(self):
        current = {'Homebrew installer': 'a' * 40, 'image': 'latest @ sha256:' + 'b' * 64}
        latest = {'Homebrew installer': 'c' * 40, 'image': 'latest @ sha256:' + 'd' * 64}
        for command in ('version', 'outdated', 'upgrade'):
            for hashes in (False, True):
                with self.subTest(command=command, hashes=hashes):
                    output = io.StringIO()
                    argv = ['dependencies.py', command] + (['--hashes'] if hashes else [])
                    with patch('sys.argv', argv), \
                            patch.object(deps, 'inventory', return_value=current), \
                            patch.object(deps, 'plan', return_value=(current, latest, {'pin': 'new'})), \
                            patch('builtins.input', return_value='n'), \
                            contextlib.redirect_stdout(output):
                        deps.main()
                    text = output.getvalue()
                    if hashes:
                        self.assertIn('a' * 40, text)
                        self.assertIn('sha256:' + 'b' * 64, text)
                        if command != 'version':
                            self.assertIn('c' * 40, text)
                            self.assertIn('sha256:' + 'd' * 64, text)
                    else:
                        if command != 'upgrade':
                            self.assertNotRegex(text, r'[a-f0-9]{40}')
                        self.assertNotIn('sha256:', text)
                    if command != 'version':
                        self.assertIn('(update available)', text)

    def test_checksum_fallback_and_invalid_metadata(self):
        release = {'assets': [{'name': 'archive', 'browser_download_url': 'https://example.org/archive'}]}
        with patch.object(deps, 'request', return_value=io.BytesIO(b'archive')):
            self.assertEqual(deps.asset_checksums(release, ['archive']),
                             hashlib.sha256(b'archive').hexdigest() + '  archive\n')
        release['assets'][0]['digest'] = 'invalid'
        with self.assertRaises(ValueError):
            deps.asset_checksums(release, ['archive'])

    def test_redirect_strips_bearer_on_other_host(self):
        from urllib.request import Request
        handler = deps.SafeRedirectHandler()
        request = Request('https://api.github.com/start', headers={'Authorization': 'Bearer private'})
        same = handler.redirect_request(request, None, 302, 'Found', {}, 'https://api.github.com/end')
        other = handler.redirect_request(request, None, 302, 'Found', {}, 'https://example.org/end')
        self.assertEqual(same.get_header('Authorization'), 'Bearer private')
        self.assertIsNone(other.get_header('Authorization'))

    def test_registry_resolution_and_official_images(self):
        for repository, host, path in (
            ('debian', 'registry-1.docker.io', 'library/debian'),
            ('ghcr.io/example/app', 'ghcr.io', 'example/app'),
            ('quay.io/example/app', 'quay.io', 'example/app'),
        ):
            with self.subTest(repository=repository), patch.object(
                    deps, 'fetch', side_effect=[b'{"token":"private"}', b'manifest']) as fetch:
                self.assertEqual(deps.image_digest(repository, 'latest'), hashlib.sha256(b'manifest').hexdigest())
                self.assertEqual(fetch.call_args.args[0], f'https://{host}/v2/{path}/manifests/latest')
        self.assertEqual(deps.IMAGE.findall('ghcr.io/example/app:latest@sha256:' + 'a' * 64)[0][0],
                         'ghcr.io/example/app')

    def test_failed_replacement_rolls_back_previous_files(self):
        first, second = 'build/tests/act.version', 'build/code-server.version'
        before = {name: (self.root / name).read_bytes() for name in (first, second)}
        replace = deps.os.replace
        def fail_second(source, target):
            if target == self.root / second:
                raise OSError('simulated replace failure')
            replace(source, target)
        with patch.object(deps.os, 'replace', side_effect=fail_second):
            with self.assertRaises(OSError):
                deps.apply_edits(self.root, {first: 'changed', second: 'changed'})
        self.assertEqual({name: (self.root / name).read_bytes() for name in before}, before)


if __name__ == '__main__':
    unittest.main()
