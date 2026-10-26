#!/usr/bin/env python3
"""Inspect repository pins and prepare an interactive upgrade plan with rollback on write failure."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import quote, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

ROOT = Path(__file__).resolve().parents[2]
ACTION = re.compile(r'uses: ([\w-]+/[\w-]+)@([a-f0-9]{40}) # (\S+)')
IMAGE = re.compile(r'(?<![\w./:-])([\w./-]+(?::[0-9]+)?):([\w.-]+)@sha256:([a-f0-9]{64})')
WORKFLOW = '.github/workflows/container.yaml'
COMPOSE = ('compose.yaml', 'compose.backup.yaml', 'compose.backup-local.yaml',
           'build/tests/actions.sh', 'build/Dockerfile', 'build/tools/restore-home.sh', WORKFLOW)


class SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        redirected = super().redirect_request(request, fp, code, message, headers, newurl)
        if redirected is not None and (urlsplit(request.full_url).scheme, urlsplit(request.full_url).netloc) != (urlsplit(newurl).scheme, urlsplit(newurl).netloc):
            redirected.remove_header('Authorization')
        return redirected


OPENER = build_opener(SafeRedirectHandler())


def request(url, headers=None):
    headers = {'User-Agent': 'code-server-aio-dependencies', **(headers or {})}
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token and url.startswith('https://api.github.com/'):
        headers['Authorization'] = f'Bearer {token}'
    return OPENER.open(Request(url, headers=headers), timeout=60)


def fetch(url, headers=None):
    with request(url, headers) as response:
        return response.read()


def checksum(url):
    digest = hashlib.sha256()
    with request(url) as response:
        while block := response.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


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
            digest = checksum(asset['browser_download_url'])
        lines.append(f'{digest}  {name}\n')
    return ''.join(lines)


def image_digest(repo, tag):
    first, separator, rest = repo.partition('/')
    if separator and ('.' in first or ':' in first or first == 'localhost'):
        registry, repository = first, rest
    else:
        registry, repository = 'registry-1.docker.io', repo if separator else 'library/' + repo
    if registry in ('docker.io', 'index.docker.io'):
        registry = 'registry-1.docker.io'
    # Only supported registries may receive authentication requests. Fail rather
    # than silently resolving an unknown registry's image against Docker Hub.
    authentication = {
        'registry-1.docker.io': ('https://auth.docker.io/token', 'registry.docker.io'),
        'ghcr.io': ('https://ghcr.io/token', 'ghcr.io'),
        'quay.io': ('https://quay.io/v2/auth', 'quay.io'),
    }
    if registry not in authentication:
        raise ValueError('Unsupported image registry: ' + registry)
    endpoint, service = authentication[registry]
    token = json.loads(fetch(endpoint + '?service=' + service + '&scope='
                             + quote(f'repository:{repository}:pull')))
    token = token.get('token') or token['access_token']
    manifest = fetch(f'https://{registry}/v2/{repository}/manifests/{tag}', {
        'Authorization': f'Bearer {token}',
        'Accept': ', '.join(('application/vnd.oci.image.index.v1+json',
                             'application/vnd.docker.distribution.manifest.list.v2+json',
                             'application/vnd.oci.image.manifest.v1+json',
                             'application/vnd.docker.distribution.manifest.v2+json')),
    })
    return hashlib.sha256(manifest).hexdigest()


def apply_edits(root, edits):
    """Stage every write first; restore originals if a replacement fails.

    A process/host crash during replacement still requires reviewing git diff;
    multiple paths cannot be committed atomically with filesystem rename.
    """
    staged, backups, replaced = {}, {}, []

    def stage(path, content):
        fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as output:
                os.fchmod(output.fileno(), path.stat().st_mode & 0o777)
                output.write(content)
        except BaseException:
            os.unlink(temporary)
            raise
        return temporary

    try:
        for name, content in edits.items():
            path = root / name
            backups[name] = stage(path, path.read_bytes())
            staged[name] = stage(path, content.encode())
        try:
            for name, temporary in staged.items():
                os.replace(temporary, root / name)
                replaced.append(name)
        except OSError:
            for name in reversed(replaced):
                os.replace(backups[name], root / name)
            raise
    finally:
        for temporary in (*staged.values(), *backups.values()):
            if os.path.exists(temporary):
                os.unlink(temporary)


def inventory(root):
    result = {
        'code-server': (root / 'build/code-server.version').read_text().strip(),
        'act': (root / 'build/tests/act.version').read_text().strip(),
        'Homebrew installer': (root / 'build/homebrew-install.version').read_text().strip(),
    }
    for repo, sha, tag in ACTION.findall((root / WORKFLOW).read_text()):
        result[repo] = f'{tag} @ {sha}'
    images = [pin for file in COMPOSE for pin in IMAGE.findall((root / file).read_text())]
    tracks = {}
    for repo, tag, digest in images:
        tracks.setdefault(repo, set()).add(tag)
    for repo, tag, digest in images:
        label = repo + ':' + tag if len(tracks[repo]) > 1 else repo
        result[label] = f'{tag} @ sha256:{digest}'
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
    envoy_tag = None
    for file in COMPOSE:
        content = edits.get(file, (root / file).read_text())
        for repo, tag, digest in IMAGE.findall(content):
            new_tag = tag
            if repo == 'envoyproxy/envoy':
                if envoy_tag is None:
                    envoy_tag = checked(github(repo, 'releases/latest')['tag_name'], r'v\d+\.\d+\.\d+')
                new_tag = envoy_tag
            key = (repo, new_tag)
            if key not in resolved:
                resolved[key] = image_digest(repo, new_tag)
            new_digest = resolved[key]
            label = repo if repo in current else repo + ':' + tag
            latest[label] = f'{new_tag} @ sha256:{new_digest}'
            content = content.replace(f'{repo}:{tag}@sha256:{digest}', f'{repo}:{new_tag}@sha256:{new_digest}')
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
    visible = ({name: value for name, value in current.items() if value != latest[name]}
               if args.command == 'outdated' else current)
    show(visible, latest, hide_hashes=not args.hashes)
    if not edits:
        print('All repository pins are current.')
        return
    if args.command == 'outdated':
        print('Outdated repository pins found. Run just upgrade to review and apply updates.')
        return
    if current.get('Homebrew installer') != latest.get('Homebrew installer'):
        print('Review installer changes: https://github.com/Homebrew/install/compare/'
              + current['Homebrew installer'] + '...' + latest['Homebrew installer'])
    print('\nFiles to update:\n' + '\n'.join(f'  {path}' for path in sorted(edits)))
    try:
        answer = input('Update these repository pins? [y/N] ')
    except EOFError:
        answer = ''
    if answer.strip().lower() not in ('y', 'yes'):
        print('Cancelled; no files changed.')
        return
    apply_edits(ROOT, edits)
    print('Updated repository pins. Run just test and review git diff before committing. Rebuild/redeploy to apply.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        sys.exit(f'Dependency lookup/update failed: {error}')
