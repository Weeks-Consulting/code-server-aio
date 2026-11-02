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
# Fetch only the installer; manage.py selects the release and verifies its bundle.
release=https://github.com/Weeks-Consulting/code-server-aio/releases/latest/download
for name in manage.py SHA256SUMS; do
    curl --proto '=https' --proto-redir '=https' --fail --silent --show-error --location --retry 3 \
        "$release/$name" --output "$staging/$name" ||
        { echo 'Latest release files are unavailable; if a release was just published, retry after it finishes.' >&2; exit 1; }
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
python3 "$staging/manage.py" install "$@"
