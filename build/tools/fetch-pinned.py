#!/usr/bin/env python3
"""Download a pinned image package and verify it before installation."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys


def artifact(packages, name, architecture):
    package = packages[name]
    version = package['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid pinned version')
    if architecture not in ('amd64', 'arm64'):
        raise ValueError('Unsupported architecture: ' + architecture)
    if name == 'code-server':
        filename = f'code-server_{version}_{architecture}.deb'
        repository = 'coder/code-server'
    elif name == 'envoy':
        arch = {'amd64': 'x86_64', 'arm64': 'aarch_64'}[architecture]
        filename = f'envoy-{version}-linux-{arch}'
        repository = 'envoyproxy/envoy'
    else:
        raise ValueError('Unsupported package: ' + name)
    checksum = package['sha256'][filename]
    if not re.fullmatch(r'[a-f0-9]{64}', checksum):
        raise ValueError('Invalid pinned checksum')
    return f'https://github.com/{repository}/releases/download/v{version}/{filename}', checksum


def fetch(manifest, name, architecture, destination):
    url, checksum = artifact(json.loads(Path(manifest).read_text())['packages'], name, architecture)
    path = Path(destination)
    try:
        subprocess.run([
            'curl', '--fail', '--silent', '--show-error', '--location', '--retry', '3',
            '--connect-timeout', '10', '--max-time', '300', url, '--output', str(path),
        ], check=True)
        with path.open('rb') as source:
            actual = hashlib.file_digest(source, 'sha256').hexdigest()
        if actual != checksum:
            raise ValueError(f'SHA256 mismatch for {name}: {actual}')
    except Exception:
        path.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    fetch(*sys.argv[1:])
