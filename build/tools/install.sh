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
curl --proto '=https' --proto-redir '=https' --fail --silent --show-error --location --retry 3 \
    https://api.github.com/repos/Weeks-Consulting/code-server-aio/releases/latest \
    --output "$staging/release.json"
version=$(python3 -c '
import json, re, sys
release = json.load(open(sys.argv[1]))
tag = release["tag_name"]
if not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[1-9]\d*)?", tag):
    sys.exit("Invalid stable release tag: " + tag)
# Installer assets upload only after the release images are tested and published.
if not {"manage.py", "SHA256SUMS"} <= {asset.get("name") for asset in release.get("assets", [])}:
    sys.exit("Release " + tag + " is still publishing; try again later.")
print(tag)' "$staging/release.json")
for name in manage.py SHA256SUMS; do
    curl --proto '=https' --proto-redir '=https' --fail --silent --show-error --location --retry 3 \
        "https://github.com/Weeks-Consulting/code-server-aio/releases/download/$version/$name" \
        --output "$staging/$name"
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
# Install the release verified above unless the caller selects a release or repository.
select_release=true
for argument do
    case "$argument" in --version|--version=*|--repository|--repository=*) select_release=false ;; esac
done
if $select_release; then set -- --version "$version" "$@"; fi
python3 "$staging/manage.py" install "$@"
