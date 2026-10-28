#!/usr/bin/env python3
"""Inspect and synchronize the pin manifest; prepare upgrades with rollback on write failure."""
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
ACTION = re.compile(r'(?m)^(?P<prefix>[ \t]*(?:-[ \t]+)?uses:[ \t]*)(?P<reference>[^\s#]+)(?:[ \t]+#[ \t]*(?P<version>\S+))?')
IMAGE = re.compile(r'(?<![\w./:$-])([a-z0-9][a-z0-9_./-]*(?::[0-9]+)?):([\w.-]+)(?:@([^\s\"\'{}]+))?')
WORKFLOW = '.github/workflows/container.yaml'
COMPOSE = ('compose.yaml', 'compose.backup-common.yaml',
           'build/tests/actions.sh', 'build/tests/smoke.sh', 'build/tests/compose.backup-s3.yaml',
           'build/Dockerfile', 'build/tools/restore-home.sh', WORKFLOW)


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


PIN_FILE = 'build/versions.json'
PIN_FILES = {'runtime': PIN_FILE, 'build': 'build/build-versions.json'}


def load_pins(root, scope=None):
    pins = {'packages': {}, 'images': {}, 'actions': {}}
    for filename in ([PIN_FILES[scope]] if scope else PIN_FILES.values()):
        manifest = json.loads((root / filename).read_text())
        for section in pins:
            overlap = pins[section].keys() & manifest[section].keys()
            if overlap:
                raise ValueError(f'Duplicate pins in {filename}: {sorted(overlap)}')
            pins[section].update(manifest[section])
    for name, package in pins['packages'].items():
        version = checked(package['version'], r'\d+\.\d+\.\d+')
        assets = {
            'code-server': {f'code-server_{version}_{arch}.deb' for arch in ('amd64', 'arm64')},
            'envoy': {f'envoy-{version}-linux-{arch}' for arch in ('aarch_64', 'x86_64')},
            'act': {f'act_{system}_{arch}.tar.gz' for system in ('Linux', 'Darwin')
                    for arch in ('arm64', 'x86_64')},
        }
        if name not in assets or set(package['sha256']) != assets[name]:
            raise ValueError(f'{name}: checksums are required for every supported package asset')
        for digest in package['sha256'].values():
            checked(digest, r'[a-f0-9]{64}')
    for digest in pins['images'].values():
        checked(digest, r'[a-f0-9]{64}')
    for pin in pins['actions'].values():
        checked(pin['version'], r'v\d+(?:\.\d+){0,2}')
        checked(pin['sha'], r'[a-f0-9]{40}')
    return pins


def inventory(root, pins=None, scope=None):
    pins = load_pins(root, scope) if pins is None else pins
    result = {name: package['version'] for name, package in pins['packages'].items()}
    result.update({repo: f'{pin["version"]} @ {pin["sha"]}'
                   for repo, pin in pins['actions'].items()})
    tracks = {}
    for image in pins['images']:
        repo, tag = image.rsplit(':', 1)
        tracks.setdefault(repo, []).append(tag)
    for image, digest in pins['images'].items():
        repo, tag = image.rsplit(':', 1)
        result[repo if len(tracks[repo]) == 1 else image] = f'{tag} @ sha256:{digest}'
    return result


def reference_files(root):
    """Discover dependency-bearing source files, including newly added workflows/tests."""
    paths = set(COMPOSE)
    for pattern in ('compose*.yaml', 'build/**/Dockerfile*', 'build/**/*.sh',
                    'build/**/compose*.yaml', '.github/workflows/*.yaml', '.github/workflows/*.yml'):
        paths.update(str(path.relative_to(root)) for path in root.glob(pattern))
    return sorted(paths)


def references(root, pins):
    """Render required pins even when their digest/SHA has been removed."""
    edits = {}
    for file in reference_files(root):
        original = (root / file).read_text()

        def image(match):
            repo, tag, _ = match.groups()
            key = repo + ':' + tag
            if repo in ('dir', 'docker-daemon'):
                return match[0]
            if key not in pins['images'] and not (
                    '/' in repo or declaration or 'docker run ' in line or match[3]):
                return match[0]
            if key not in pins['images']:
                raise ValueError(f'{file}: image {key} has no manifest pin')
            return f'{key}@sha256:{pins["images"][key]}'

        lines = []
        for line in original.splitlines(keepends=True):
            # The shipped workspace image is this project's mutable output;
            # compose.build.yaml uses the image built from the local checkout.
            if ('image: ${CODE_SERVER_IMAGE:-' in line or
                    (file == 'compose.build.yaml' and line.strip() == 'image: code-server-cloudflare:local')):
                lines.append(line)
                continue
            if line.lstrip().startswith('#'):
                lines.append(line)
                continue
            # Bare image names in Docker/Compose still require a tag and digest.
            declaration = re.match(r'\s*(?:FROM|image:|container:|container-image:)\s+([^\s]+)', line)
            # The host matrix's Debian literal is checked separately below;
            # its macOS entry intentionally has no container.
            if (declaration and line.strip() != 'container: ${{ matrix.container-image }}'
                    and not IMAGE.fullmatch(declaration[1])):
                raise ValueError(f'{file}: image {declaration[1]} requires a manifest tag and digest')
            for key in pins['images']:
                repo = key.rsplit(':', 1)[0]
                if re.search(r'(?<![\w./:-])' + re.escape(repo) + r'(?=[\s\"\']|$)', line):
                    raise ValueError(f'{file}: image {repo} requires a manifest tag and digest')
            lines.append(IMAGE.sub(image, line))
        content = ''.join(lines)
        if file.startswith('.github/workflows/'):
            def action(match):
                reference = match['reference']
                if reference.startswith('./'):
                    return match[0]
                repo, _, _ = reference.partition('@')
                if repo not in pins['actions']:
                    raise ValueError(f'{file}: action {repo} has no manifest pin')
                pin = pins['actions'][repo]
                return f'{match["prefix"]}{repo}@{pin["sha"]} # {pin["version"]}'
            content = ACTION.sub(action, content)
        if content != original:
            edits[file] = content
    return edits


def plan(root, prepare=True, scope=None):
    current = inventory(root, scope=scope)
    pins = load_pins(root, scope)
    changed = False
    for label, repo in (('code-server', 'coder/code-server'),
                        ('envoy', 'envoyproxy/envoy'), ('act', 'nektos/act')):
        if label not in pins['packages']:
            continue
        release = github(repo, 'releases/latest')
        version = checked(release['tag_name'], r'v\d+\.\d+\.\d+')[1:]
        if version != pins['packages'][label]['version']:
            changed = True
            pins['packages'][label]['version'] = version
            names = ([f'code-server_{version}_{arch}.deb' for arch in ('amd64', 'arm64')]
                     if label == 'code-server' else
                     [f'envoy-{version}-linux-{arch}' for arch in ('aarch_64', 'x86_64')]
                     if label == 'envoy' else
                     [f'act_{system}_{arch}.tar.gz' for system in ('Linux', 'Darwin')
                      for arch in ('arm64', 'x86_64')])
            if prepare:
                pins['packages'][label]['sha256'] = {
                    line.split()[1]: line.split()[0]
                    for line in asset_checksums(release, names).splitlines()}
    for repo, pin in pins['actions'].items():
        release = github(repo, 'releases/latest')
        tag = checked(release['tag_name'], r'v\d+(?:\.\d+){0,2}')
        sha = checked(github(repo, 'commits/' + quote(tag, safe=''))['sha'], r'[a-f0-9]{40}')
        changed |= pin != {'version': tag, 'sha': sha}
        pins['actions'][repo] = {'version': tag, 'sha': sha}
    for image, digest in pins['images'].items():
        repo, tag = image.rsplit(':', 1)
        new_digest = image_digest(repo, tag)
        changed |= new_digest != digest
        pins['images'][image] = new_digest
    # Inventory uses the same formatting for local and proposed pins.
    latest = inventory(root, pins)
    combined = load_pins(root)
    for section in pins:
        combined[section].update(pins[section])
    edits = references(root, combined)
    if changed:
        for selected in ([scope] if scope else PIN_FILES):
            filename = PIN_FILES[selected]
            original = load_pins(root, selected)
            updated = {section: {key: pins[section][key] for key in values}
                       for section, values in original.items()}
            if updated != original:
                edits[filename] = json.dumps(updated, indent=2) + '\n'
    return current, latest, edits


def show(current, latest=None, hide_hashes=True):
    def display(value):
        if not hide_hashes:
            return value
        return value.split(' @ ', 1)[0]

    for name, value in current.items():
        print(f'{name}: {display(value)}', flush=True)
        if latest is not None:
            status = ' (current)' if value == latest[name] else ' (update available)'
            print(f'  latest: {display(latest[name])}{status}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('version', 'outdated', 'upgrade', 'sync', 'check'))
    parser.add_argument('--scope', choices=('runtime', 'build'), default='runtime')
    parser.add_argument('--hashes', action='store_true', help='Show commit SHAs and image digests')
    args = parser.parse_args()
    if args.command in ('sync', 'check'):
        edits = references(ROOT, load_pins(ROOT))
        if args.command == 'check' and edits:
            raise ValueError('Dependency references must match the pin manifests; run just sync-pins')
        if args.command == 'sync':
            apply_edits(ROOT, edits)
        return
    if args.command == 'version':
        show(inventory(ROOT, scope=args.scope), hide_hashes=not args.hashes)
        return
    print('Looking up upstream releases, Actions, and service image digests...', flush=True)
    current, latest, edits = plan(ROOT, prepare=args.command == 'upgrade', scope=args.scope)
    visible = ({name: value for name, value in current.items() if value != latest[name]}
               if args.command == 'outdated' else current)
    show(visible, latest, hide_hashes=not args.hashes)
    if not edits:
        print('All repository pins are current.')
        return
    if args.command == 'outdated':
        print('Outdated repository pins found. Run just ' + ('build-upgrade' if args.scope == 'build' else 'upgrade') + ' to review and apply updates.')
        return
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
