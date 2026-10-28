"""Exercise the pinned GitHub CLI download even when a capable CLI is installed."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import manage


def main():
    with tempfile.TemporaryDirectory(prefix='github-cli-check-') as directory:
        original_path = os.environ.get('PATH')
        try:
            # ensure_gh uses Python's archive readers and needs no external tools.
            os.environ['PATH'] = ''
            binary = manage.ensure_gh(Path(directory))
        finally:
            if original_path is None:
                os.environ.pop('PATH', None)
            else:
                os.environ['PATH'] = original_path
        subprocess.run([binary, 'attestation', 'verify', '--help'], check=True,
                       stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
