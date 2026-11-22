"""Publish installer assets without replacing completed uploads."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


# Flat names: the manifest records the published asset names, which match these paths.
INSTALLER_FILES = (Path('install.sh'), Path('manage.py'), Path('code-server-aio.tar.gz'))
CHECKSUMS = Path('SHA256SUMS')


def gh(*args):
    return subprocess.check_output(['gh', *args])


def pages(endpoint):
    return [entry for page in json.loads(gh('api', '--paginate', '--slurp', endpoint)) for entry in page]


def parse_checksums(data):
    checksums = {}
    # Same rules as install.sh and manage.py: 64-hex digests, unique names.
    for line in data.decode().splitlines():
        if not line.strip():
            continue
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or not re.fullmatch(r'[a-f0-9]{64}', fields[0]) or fields[1] in checksums:
            raise ValueError(f'Invalid or duplicate checksum line: {line!r}')
        checksums[fields[1]] = fields[0]
    return checksums


def publish(repository, tag):
    required = {path.name for path in (*INSTALLER_FILES, CHECKSUMS)}
    releases = [release for release in pages(f'repos/{repository}/releases?per_page=100')
                if release['tag_name'] == tag]
    if len(releases) > 1:
        raise ValueError(f'Multiple releases found for {tag}. Resolve the duplicate releases before retrying.')
    release = releases[0] if releases else None
    uploaded, starters = {}, []
    if release:
        for asset in pages(f'repos/{repository}/releases/{release["id"]}/assets?per_page=100'):
            name = asset['name']
            if name not in required:
                continue
            if asset['state'] == 'uploaded':
                uploaded[name] = asset
            elif asset['state'] == 'starter' and asset['size'] == 0:
                # GitHub can leave an empty placeholder after a failed upload.
                starters.append(asset['id'])
            else:
                raise ValueError(f'Unexpected upload state for {tag} asset {name}. Inspect it before retrying.')

    def download(asset):
        return gh('api', '-H', 'Accept: application/octet-stream',
                  f'repos/{repository}/releases/assets/{asset["id"]}')

    # An existing manifest defines the checksums missing assets must satisfy.
    # Before the manifest is uploaded, reproducible build output can resume the upload.
    manifest = uploaded.get(CHECKSUMS.name)
    checksums = parse_checksums(download(manifest) if manifest else CHECKSUMS.read_bytes())
    missing = []
    for path in INSTALLER_FILES:
        asset = uploaded.get(path.name)
        if asset is None:
            missing.append(str(path))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        elif asset.get('digest'):
            digest = asset['digest'].removeprefix('sha256:')
        else:
            digest = hashlib.sha256(download(asset)).hexdigest()
        if checksums.get(path.name) != digest:
            raise ValueError(f'{tag} asset {path.name} does not match the release checksums. '
                             'Recover the matching files or publish a new tag.')

    if not release:
        gh('release', 'create', tag, '--repo', repository, '--verify-tag',
           '--title', tag, '--generate-notes', '--draft', '--prerelease', '--latest=false')
    for asset_id in starters:
        gh('api', '--method', 'DELETE', f'repos/{repository}/releases/assets/{asset_id}')
    if missing:
        gh('release', 'upload', tag, *missing, '--repo', repository)
    if not manifest:
        gh('release', 'upload', tag, str(CHECKSUMS), '--repo', repository)
    if not release or release['draft']:
        gh('release', 'edit', tag, '--repo', repository, '--draft=false', '--prerelease', '--latest=false')
    print(f'Installer assets are complete for {tag}.')


if __name__ == '__main__':
    try:
        publish(os.environ['GH_REPO'], os.environ['RELEASE_TAG'])
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
