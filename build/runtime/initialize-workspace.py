"""Prepare the mounted workspace before starting code-server through Supervisor."""

import os
from pathlib import Path
import pwd
import subprocess
import sys

SYSTEM_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
SUPERVISORCTL = '/usr/bin/supervisorctl'


def initialize():
    # Root setup must never resolve commands from the user's Homebrew directory.
    os.environ['PATH'] = SYSTEM_PATH
    # Only Envoy shares this volume; code-server has no TCP listener.
    socket_dir = Path('/run/code-server')
    socket_dir.mkdir(parents=True, exist_ok=True)
    os.chown(socket_dir, 10000, 10000)
    socket_dir.chmod(0o750)
    user = pwd.getpwnam('cloud-user')
    home = Path(user.pw_dir)
    home.mkdir(parents=True, exist_ok=True)
    # Preserve ownership of existing contents, including nested-container files.
    os.chown(home, user.pw_uid, user.pw_gid)
    subprocess.run(['/bin/bash', '/usr/local/lib/preinstall.sh'], check=True)
    subprocess.run([SUPERVISORCTL, 'start', 'code-server'], check=True)


def main():
    try:
        initialize()
    except Exception as error:
        result = error.returncode if isinstance(error, subprocess.CalledProcessError) else 1
        print(f'Workspace initialization failed ({result}): {error}; restarting the container.', file=sys.stderr, flush=True)
        subprocess.run([SUPERVISORCTL, 'shutdown'], check=False)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
