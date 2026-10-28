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
                    url, checksum = fetch_pinned.artifact(packages, name, arch)
                    self.assertTrue(url.startswith('https://github.com/'))
                    self.assertEqual(len(checksum), 64)
        with self.assertRaises(ValueError):
            fetch_pinned.artifact(packages, 'envoy', 'unsupported')

    def test_download_verification_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, output = root / 'pins.json', root / 'package.deb'
            manifest.write_text(json.dumps({'packages': {'code-server': {
                'version': '1.2.3', 'sha256': {'code-server_1.2.3_amd64.deb':
                                           hashlib.sha256(b'verified').hexdigest()}}}}))
            for data in (b'verified', b'corrupt'):
                with self.subTest(data=data), patch.object(fetch_pinned.subprocess, 'run',
                        side_effect=lambda *args, **kwargs: output.write_bytes(data)):
                    if data == b'verified':
                        fetch_pinned.fetch(manifest, 'code-server', 'amd64', output)
                        self.assertEqual(output.read_bytes(), data)
                    else:
                        with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                            fetch_pinned.fetch(manifest, 'code-server', 'amd64', output)
                        self.assertFalse(output.exists())
