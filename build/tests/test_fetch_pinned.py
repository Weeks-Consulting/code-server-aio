"""Verify pinned download selection and rejection of corrupt artifacts offline."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('fetch_pinned', ROOT / 'build/tools/fetch-pinned.py')
fetch_pinned = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_pinned)


class FetchPinnedTests(unittest.TestCase):
    def test_all_shipped_architectures_have_pins(self):
        packages = json.loads((ROOT / 'build/versions.json').read_text())['packages']
        for name in ('code-server', 'envoy'):
            for arch in ('amd64', 'arm64'):
                with self.subTest(name=name, arch=arch):
                    package = packages[name]
                    asset_arch = {'amd64': 'x86_64', 'arm64': 'aarch_64'}[arch]
                    filename = (f'code-server_{package["version"]}_{arch}.deb' if name == 'code-server'
                                else f'envoy-{package["version"]}-linux-{asset_arch}')
                    url, checksum = fetch_pinned.artifact(
                        name, package['version'], arch, package['sha256'][filename])
                    self.assertTrue(url.startswith('https://github.com/'))
                    self.assertTrue(url.endswith('/' + filename))
                    self.assertEqual(len(checksum), 64)
        with self.assertRaises(ValueError):
            fetch_pinned.artifact('envoy', '1.2.3', 'unsupported', 'a' * 64)

    def test_invalid_build_args_are_rejected_before_download(self):
        for name, version, arch, checksum in (
            ('other', '1.2.3', 'amd64', 'a' * 64),
            ('envoy', '../latest', 'amd64', 'a' * 64),
            ('envoy', '1.2.3', 'amd64', ''),
            ('envoy', '1.2.3', 'amd64', 'invalid'),
        ):
            with self.subTest(name=name, version=version, checksum=checksum), patch.object(
                    fetch_pinned.subprocess, 'run') as run:
                with self.assertRaises(ValueError):
                    fetch_pinned.fetch(name, version, arch, checksum, '/unused')
                run.assert_not_called()

    def test_download_verification_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'package.deb'
            checksum = hashlib.sha256(b'verified').hexdigest()
            for data in (b'verified', b'corrupt'):
                with self.subTest(data=data), patch.object(fetch_pinned.subprocess, 'run',
                        side_effect=lambda *args, **kwargs: output.write_bytes(data)):
                    if data == b'verified':
                        fetch_pinned.fetch('code-server', '1.2.3', 'amd64', checksum, output)
                        self.assertEqual(output.read_bytes(), data)
                    else:
                        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                            fetch_pinned.fetch('code-server', '1.2.3', 'amd64', checksum, output)
                        self.assertFalse(output.exists())
