#!/bin/sh
# Bootstrap; deployment installation lives in manage.py.
set -eu
# A piped script has $0=sh; never resolve a checkout relative to its caller.
if [ "${0##*/}" = install.sh ] && [ -f "$0" ]; then
    repository_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
    if [ -f "$repository_root/manage.py" ]; then
        exec python3 "$repository_root/manage.py" install "$@"
    fi
fi
command -v python3 >/dev/null || { echo 'Install python3 before running this installer.' >&2; exit 1; }
# Handle help and previews before creating staging files or requiring curl.
# The repository and optional tag are the only values passed back to the shell.
selection=$(python3 - "$@" <<'PYSELECT'
import argparse
import os
from pathlib import Path
import re
import sys
parser = argparse.ArgumentParser(prog='sh -s --', allow_abbrev=False,
                                 description='Install a published code-server deployment.')
parser.add_argument('--directory', metavar='PATH',
                    help='Installation directory (default: ~/code-server, or /opt/code-server as root)')
parser.add_argument('--repository', metavar='OWNER/REPO', default='Weeks-Consulting/code-server-aio',
                    help='Install from a fork')
parser.add_argument('--version', metavar='TAG', help='Select a release instead of the latest stable release')
parser.add_argument('--dry-run', action='store_true', help='Preview the installation without downloading release files')
# manage.py install accepts no other options; reject typos before downloading.
args = parser.parse_args(sys.argv[1:])
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', args.repository):
    sys.exit('Repository must be owner/repo.')
if args.version is not None and not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[1-9]\d*)?', args.version):
    sys.exit('Expected a release tag such as v4.144.0 or v4.144.0-1.')
if args.directory == '':
    parser.error('--directory requires a nonempty path')
if args.dry_run:
    default = '/opt/code-server' if os.geteuid() == 0 else Path.home() / 'code-server'
    directory = Path(os.path.abspath(args.directory or default))
    if args.version:
        print(f'Would use release {args.version} from {args.repository}.')
        print(f'Would select ghcr.io/{args.repository.lower()}:{args.version[1:]} unless .env pins an image digest.')
    else:
        print(f'Would resolve the latest stable release of {args.repository} and use its versioned image unless .env pins an image digest.')
    print(f'Would install or refresh deployment files in {directory}.')
    print('Would preserve existing .env settings and generate a backup key if it is missing or empty.')
    print('Dry run complete. No releases downloaded, files changed, or services started.')
else:
    print('install', args.repository, args.version or '')
PYSELECT
)
case "$selection" in
    'install '*)
        release_args=${selection#install }
        repository=${release_args%% *}
        version=${release_args#* }
        ;;
    *)
        printf '%s\n' "$selection"
        exit 0
        ;;
esac
command -v curl >/dev/null || { echo 'Install curl or use manage install from a checkout.' >&2; exit 1; }
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
if [ -z "$version" ]; then
    curl --proto '=https' --proto-redir '=https' --fail --silent --show-error --location --retry 3 \
        "https://api.github.com/repos/$repository/releases/latest" --output "$staging/release.json" ||
        { echo 'Latest release files are unavailable; if a release was just published, retry after it finishes.' >&2; exit 1; }
    version=$(python3 - "$staging/release.json" <<'PYRELEASE'
import json
import re
import sys
with open(sys.argv[1]) as source:
    release = json.load(source)
tag = release.get('tag_name', '')
if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'v\d+\.\d+\.\d+(?:-[1-9]\d*)?', tag):
    sys.exit('GitHub returned an invalid stable release tag')
print(tag)
PYRELEASE
    )
fi
release="https://github.com/$repository/releases/download/$version"
for name in manage.py SHA256SUMS; do
    curl --proto '=https' --proto-redir '=https' --fail --silent --show-error --location --retry 3 \
        "$release/$name" --output "$staging/$name" ||
        { echo 'Selected release files are unavailable; if a release was just published, retry after it finishes.' >&2; exit 1; }
done
# Check manage.py against the release's checksums before executing it.
python3 - "$staging" <<'PY'
import hashlib
from pathlib import Path
import re
import sys
root = Path(sys.argv[1])
# Same rules as manage.py and publish-release.py: 64-hex digests, unique names.
checksums = {}
for line in (root / 'SHA256SUMS').read_text().splitlines():
    if not line.strip():
        continue
    fields = line.split(maxsplit=1)
    if len(fields) != 2 or not re.fullmatch(r'[a-f0-9]{64}', fields[0]) or fields[1] in checksums:
        sys.exit('Bootstrap checksum manifest SHA256SUMS is malformed')
    checksums[fields[1]] = fields[0]
if checksums.get('manage.py') != hashlib.sha256((root / 'manage.py').read_bytes()).hexdigest():
    sys.exit('Bootstrap manage.py checksum missing or incorrect')
PY
python3 "$staging/manage.py" install "$@" --version "$version"
