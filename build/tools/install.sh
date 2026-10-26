#!/bin/sh
# Compatibility bootstrap; deployment installation lives in manage.py.
set -eu
repository_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
if [ -f "$repository_root/manage.py" ]; then
    exec python3 "$repository_root/manage.py" install "$@"
fi
command -v python3 >/dev/null || { echo 'Install python3 before running this installer.' >&2; exit 1; }
command -v curl >/dev/null || { echo 'Install curl or use manage install from a checkout.' >&2; exit 1; }
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT HUP INT TERM
curl --fail --silent --show-error --location --retry 3 \
    https://github.com/Weeks-Consulting/code-server-aio/releases/latest/download/manage.py \
    --output "$staging/manage.py"
python3 "$staging/manage.py" install "$@"
