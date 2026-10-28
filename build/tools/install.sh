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
# Embedded from manage.py so the bootstrap never executes an unverified helper.
gh_binary=$(python3 - "$staging/gh" <<'GH_PY'
import hashlib
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request

GH_PIN = {'version': '2.103.3', 'sha256': {'gh_2.103.3_linux_amd64.tar.gz': '4c806978ffce1cec70853302a56d70ac6da4f19bae7adca4e1a8ab00633dd91d', 'gh_2.103.3_linux_arm64.tar.gz': 'ee32df2323c8847f5fac6cb6b4b69ca75828e9329a8f22f677021f7b3306ce63', 'gh_2.103.3_macOS_amd64.zip': 'a2f86418f4350ae6e9ec60d5000bdf7665728436946a97fc27fe50e6ae752682', 'gh_2.103.3_macOS_arm64.zip': 'd700207eae9ae98fe2b89535c2c087c07823d4628c56364695a5a07ef311c062'}}


def ensure_gh(directory=None):
    """Use a capable CLI, or fetch a checksum-pinned binary without a package repo."""
    import atexit
    import io
    import tarfile
    import zipfile
    existing = shutil.which('gh')
    if existing and subprocess.run([existing, 'attestation', 'verify', '--help'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        return existing
    system = {'Linux': 'linux', 'Darwin': 'macOS'}.get(platform.system())
    arch = {'x86_64': 'amd64', 'AMD64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine())
    if not system or not arch:
        raise ValueError('GitHub CLI download supports Linux/macOS on amd64/arm64; install a CLI supporting gh attestation verify.')
    version = GH_PIN['version']
    suffix = 'zip' if system == 'macOS' else 'tar.gz'
    stem = f'gh_{version}_{system}_{arch}'
    filename = stem + '.' + suffix
    request = urllib.request.Request(f'https://github.com/cli/cli/releases/download/v{version}/{filename}',
                                     headers={'User-Agent': 'code-server-aio-manage'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != GH_PIN['sha256'][filename]:
        raise ValueError('GitHub CLI archive checksum mismatch')
    member = stem + '/bin/gh'
    if suffix == 'zip':
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            binary = archive.read(member)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            with archive.extractfile(member) as source:
                binary = source.read()
    if directory is None:
        temporary = tempfile.TemporaryDirectory(prefix='github-cli-')
        atexit.register(temporary.cleanup)
        directory = Path(temporary.name)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(prefix='.gh-', dir=directory)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            output.write(binary)
            os.fchmod(output.fileno(), 0o755)
        os.replace(temporary_path, directory / 'gh')
    finally:
        Path(temporary_path).unlink(missing_ok=True)
    os.environ['PATH'] = str(directory) + os.pathsep + os.environ.get('PATH', '')
    return str(directory / 'gh')

try:
    print(ensure_gh(Path(sys.argv[1])))
except (OSError, ValueError) as error:
    sys.exit('Could not obtain a GitHub CLI that supports attestation verification: ' + str(error))
GH_PY
)
PATH="$(dirname -- "$gh_binary"):$PATH"
export PATH
curl --proto '=https' --proto-redir '=https' --fail --silent --show-error --location --retry 3 \
    https://api.github.com/repos/Weeks-Consulting/code-server-aio/releases/latest \
    --output "$staging/release.json"
version=$(python3 -c 'import json,re,sys; tag=json.load(open(sys.argv[1]))["tag_name"]; assert re.fullmatch(r"v\d+\.\d+\.\d+(?:-[1-9]\d*)?", tag), "Invalid stable release tag"; print(tag)' "$staging/release.json")
for name in manage.py SHA256SUMS; do
    curl --proto '=https' --proto-redir '=https' --fail --silent --show-error --location --retry 3 \
        "https://github.com/Weeks-Consulting/code-server-aio/releases/download/$version/$name" \
        --output "$staging/$name"
done
"$gh_binary" attestation verify "$staging/SHA256SUMS" --repo Weeks-Consulting/code-server-aio \
    --signer-workflow Weeks-Consulting/code-server-aio/.github/workflows/container.yaml
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
