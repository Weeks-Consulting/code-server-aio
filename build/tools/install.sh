#!/bin/sh
# Bootstrap; deployment installation lives in manage.py.
set -eu
# A piped script has $0=sh; never resolve a checkout relative to its caller.
if [ -f "$0" ] && [ "$(basename -- "$0")" = install.sh ]; then
    repository_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
    if [ -f "$repository_root/manage.py" ]; then
        exec python3 "$repository_root/manage.py" install "$@"
    fi
fi
command -v python3 >/dev/null || { echo 'Install python3 before running this installer.' >&2; exit 1; }
command -v curl >/dev/null || { echo 'Install curl or use manage install from a checkout.' >&2; exit 1; }
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Select one release before fetching any executable code. Keep the caller's
# arguments intact; the verified installer handles the remaining options.
python3 - "$staging" "$@" <<'PYSELECT'
import argparse
from pathlib import Path
import re
import sys
parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
parser.add_argument('--repository', default='Weeks-Consulting/code-server-aio')
parser.add_argument('--version', default='')
args, _ = parser.parse_known_args(sys.argv[2:])
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', args.repository):
    sys.exit('Repository must be owner/repo.')
if args.version and not re.fullmatch(r'v\d+\.\d+\.\d+(?:-(?:0|[1-9]\d*))?', args.version):
    sys.exit('Expected a release tag such as v4.144.0-0.')
root = Path(sys.argv[1])
(root / 'repository').write_text(args.repository)
(root / 'version').write_text(args.version)
PYSELECT
repository=$(cat "$staging/repository")
version=$(cat "$staging/version")
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
if release.get('draft') or release.get('prerelease') or not re.fullmatch(r'v\d+\.\d+\.\d+(?:-(?:0|[1-9]\d*))?', tag):
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
import sys
root = Path(sys.argv[1])
checksums = dict((name, digest) for digest, name in
                 (line.split(maxsplit=1) for line in (root / 'SHA256SUMS').read_text().splitlines()))
if checksums.get('manage.py') != hashlib.sha256((root / 'manage.py').read_bytes()).hexdigest():
    sys.exit('Bootstrap manage.py checksum missing or incorrect')
PY
python3 "$staging/manage.py" install "$@" --version "$version"
