#!/usr/bin/env python3
"""Inspect repository pins and prepare an interactive, all-or-nothing upgrade plan."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
ACTION = re.compile(r'uses: ([\w-]+/[\w-]+)@([a-f0-9]{40}) # (\S+)')
IMAGE = re.compile(r'([\w/-]+):([\w.-]+)@sha256:([a-f0-9]{64})')
WORKFLOW = '.github/workflows/container.yaml'
COMPOSE = ('compose.yaml', 'compose.backup.yaml', 'compose.backup-local.yaml', 'build/tests/actions.sh')


def fetch(url, headers=None):
    headers = {'User-Agent': 'code-server-aio-dependencies', **(headers or {})}
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token and url.startswith('https://api.github.com/'):
        headers['Authorization'] = f'Bearer {token}'
    with urlopen(Request(url, headers=headers), timeout=60) as response:
        return response.read()


def github(repo, endpoint):
    return json.loads(fetch(f'https://api.github.com/repos/{repo}/{endpoint}'))


def checked(value, pattern):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ValueError(f'Invalid upstream version or digest: {value!r}')
    return value


def asset_checksums(release, names):
    assets = {asset['name']: asset for asset in release['assets']}
    lines = []
    for name in names:
        asset = assets[name]
        digest = asset.get('digest')
        if digest:
            digest = checked(digest, r'sha256:[a-f0-9]{64}')[7:]
        else:
            digest = hashlib.sha256(fetch(asset['browser_download_url'])).hexdigest()
        lines.append(f'{digest}  {name}\n')
    return ''.join(lines)


def image_digest(repo, tag):
    token = json.loads(fetch('https://auth.docker.io/token?service=registry.docker.io&scope='
                             + quote(f'repository:{repo}:pull')))['token']
    manifest = fetch(f'https://registry-1.docker.io/v2/{repo}/manifests/{tag}', {
        'Authorization': f'Bearer {token}',
        'Accept': ', '.join(('application/vnd.oci.image.index.v1+json',
                             'application/vnd.docker.distribution.manifest.list.v2+json',
                             'application/vnd.oci.image.manifest.v1+json',
                             'application/vnd.docker.distribution.manifest.v2+json')),
    })
    return hashlib.sha256(manifest).hexdigest()


def inventory(root):
    result = {
        'code-server': (root / 'build/code-server.version').read_text().strip(),
        'act': (root / 'build/tests/act.version').read_text().strip(),
        'Homebrew installer': (root / 'build/homebrew-install.version').read_text().strip(),
    }
    for repo, sha, tag in ACTION.findall((root / WORKFLOW).read_text()):
        result[repo] = f'{tag} @ {sha}'
    for file in COMPOSE:
        for repo, tag, digest in IMAGE.findall((root / file).read_text()):
            result[repo] = f'{tag} @ sha256:{digest}'
    return result


def plan(root, prepare=True):
    current = inventory(root)
    latest = dict(current)
    edits = {}
    for label, repo, version_file, checksum_file in (
        ('code-server', 'coder/code-server', 'build/code-server.version', 'build/code-server.sha256'),
        ('act', 'nektos/act', 'build/tests/act.version', 'build/tests/act.sha256'),
    ):
        release = github(repo, 'releases/latest')
        version = checked(release['tag_name'], r'v\d+\.\d+\.\d+')[1:]
        latest[label] = version
        if version != current[label]:
            names = ([f'code-server_{version}_{arch}.deb' for arch in ('amd64', 'arm64')]
                     if label == 'code-server' else
                     [f'act_{system}_{arch}.tar.gz' for system in ('Linux', 'Darwin')
                      for arch in ('arm64', 'x86_64')])
            edits[version_file] = version + '\n'
            if prepare:
                edits[checksum_file] = asset_checksums(release, names)
    commit = checked(github('Homebrew/install', 'commits/HEAD')['sha'], r'[a-f0-9]{40}')
    latest['Homebrew installer'] = commit
    if commit != current['Homebrew installer']:
        edits['build/homebrew-install.version'] = commit + '\n'
        if prepare:
            script = fetch(f'https://raw.githubusercontent.com/Homebrew/install/{commit}/install.sh')
            edits['build/homebrew-install.sha256'] = hashlib.sha256(script).hexdigest() + '  homebrew-install.sh\n'
    workflow = (root / WORKFLOW).read_text()
    for repo, sha, tag in dict.fromkeys(ACTION.findall(workflow)):
        release = github(repo, 'releases/latest')
        new_tag = checked(release['tag_name'], r'v\d+(?:\.\d+){0,2}')
        new_sha = checked(github(repo, 'commits/' + quote(new_tag, safe=''))['sha'], r'[a-f0-9]{40}')
        latest[repo] = f'{new_tag} @ {new_sha}'
        workflow = workflow.replace(f'{repo}@{sha} # {tag}', f'{repo}@{new_sha} # {new_tag}')
    if workflow != (root / WORKFLOW).read_text():
        edits[WORKFLOW] = workflow
    resolved = {}
    for file in COMPOSE:
        content = (root / file).read_text()
        for repo, tag, digest in IMAGE.findall(content):
            key = (repo, tag)
            if key not in resolved:
                resolved[key] = image_digest(repo, tag)
            new_digest = resolved[key]
            latest[repo] = f'{tag} @ sha256:{new_digest}'
            content = content.replace(f'{repo}:{tag}@sha256:{digest}', f'{repo}:{tag}@sha256:{new_digest}')
        if content != (root / file).read_text():
            edits[file] = content
    return current, latest, edits


def show(current, latest=None, hide_hashes=True):
    def display(name, value):
        if not hide_hashes:
            return value
        return 'pinned installer commit' if name == 'Homebrew installer' else value.split(' @ ', 1)[0]

    for name, value in current.items():
        print(f'{name}: {display(name, value)}', flush=True)
        if latest is not None:
            status = ' (current)' if value == latest[name] else ' (update available)'
            print(f'  latest: {display(name, latest[name])}{status}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('version', 'outdated', 'upgrade'))
    parser.add_argument('--hashes', action='store_true', help='Show commit SHAs and image digests')
    args = parser.parse_args()
    if args.command == 'version':
        show(inventory(ROOT), hide_hashes=not args.hashes)
        return
    print('Looking up upstream releases, installer commit, Actions, and service image digests...', flush=True)
    current, latest, edits = plan(ROOT, prepare=args.command == 'upgrade')
    show(current, latest, hide_hashes=not args.hashes)
    if not edits:
        print('All repository pins are current.')
        return
    if args.command == 'outdated':
        print('Outdated repository pins found. Run just upgrade to review and apply updates.')
        return
    print('\nFiles to update:\n' + '\n'.join(f'  {path}' for path in sorted(edits)))
    try:
        answer = input('Update these repository pins? [y/N] ')
    except EOFError:
        answer = ''
    if answer.strip().lower() not in ('y', 'yes'):
        print('Cancelled; no files changed.')
        return
    for file, content in edits.items():
        (ROOT / file).write_text(content)
    print('Updated repository pins. Run just test and review git diff before committing. Rebuild/redeploy to apply.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        sys.exit(f'Dependency lookup/update failed: {error}')
