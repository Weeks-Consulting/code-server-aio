"""Exercise exclusive restore reservation and SIGTERM against disposable volumes."""

import hashlib
import io
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]


def run(image):
    docker = shutil.which('docker')
    for phase in ('extract', 'replace'):
        volume = f'restore-interruption-{os.getpid()}-{phase}'
        guard = 'code-server-restore-' + hashlib.sha256(volume.encode()).hexdigest()[:24]
        with tempfile.TemporaryDirectory(prefix='.test-tmp.', dir=os.environ.get('TEST_TMPDIR', ROOT)) as directory:
            directory = Path(directory)
            archive = directory / 'input.tar.gz'
            with tarfile.open(archive, 'w:gz') as output:
                for name in ('backup', 'backup/code-server-home'):
                    member = tarfile.TarInfo(name)
                    member.type, member.mode = tarfile.DIRTYPE, 0o755
                    output.addfile(member)
                member = tarfile.TarInfo('backup/code-server-home/restored')
                member.size = len(b'restored\n')
                output.addfile(member, io.BytesIO(b'restored\n'))
            marker = directory / 'paused'
            shim = directory / 'docker'
            shim.write_text(
                f'#!{sys.executable}\n'
                'import os, pathlib, subprocess, sys, time\n'
                f'docker = {docker!r}\n'
                'args = sys.argv[1:]\n'
                # Force both callers past the advisory preflight so the second
                # must contend on the actual Docker name reservation.
                'if args[:2] == ["ps", "-q"] and "--no-trunc" not in args: sys.exit(0)\n'
                f'needle = {"tar -xzp" if phase == "extract" else "find /restore -mindepth"!r}\n'
                'if args[:1] == ["exec"] and needle in args[-1]:\n'
                '    args[-1] = "touch /tmp/restore-paused; sleep 300; " + args[-1]\n'
                '    child = subprocess.Popen([docker, *args])\n'
                '    deadline = time.monotonic() + 15\n'
                '    while subprocess.run([docker, "exec", args[2], "test", "-f", "/tmp/restore-paused"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:\n'
                '        if time.monotonic() > deadline: sys.exit(98)\n'
                '        time.sleep(0.05)\n'
                f'    pathlib.Path({str(marker)!r}).touch()\n'
                '    sys.exit(child.wait())\n'
                'os.execv(docker, [docker, *args])\n')
            shim.chmod(0o755)
            env = {**os.environ, 'PATH': str(directory) + ':' + os.environ['PATH'],
                   'RESTORE_IMAGE': image, 'BACKUP_GPG_PASSPHRASE': ''}
            command = ['sh', str(ROOT / 'build/tools/restore-home.sh'), str(archive), volume]
            process = None
            try:
                subprocess.run([docker, 'volume', 'create', volume], check=True, stdout=subprocess.DEVNULL)
                subprocess.run([docker, 'run', '--rm', '--network', 'none',
                                '--mount', f'type=volume,src={volume},dst=/data',
                                '--entrypoint', '/bin/sh', image, '-ec', 'echo original > /data/original'], check=True)
                process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                deadline = time.monotonic() + 30
                while not marker.exists():
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError('Restore did not reach interruption point: ' + str(process.communicate(timeout=5)))
                    time.sleep(0.05)
                second = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
                assert second.returncode != 0 and 'Could not reserve restore container' in second.stderr, second
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=30)
                assert process.returncode != 0, (stdout, stderr)
                absent = subprocess.run([docker, 'inspect', guard], capture_output=True)
                assert absent.returncode != 0, 'Restore reservation leaked'
                script = 'grep -qx original /data/original; test ! -e /data/restored; '
                if phase == 'extract':
                    script += 'test -z "$(find /data -maxdepth 1 -name ".restore-staging-*")"'
                else:
                    assert 'manage restore --archive' in stderr, stderr
                    rollback = list(directory.glob('pre-restore.*/original-home.tar.gz'))
                    assert len(rollback) == 1, rollback
                    with tarfile.open(rollback[0]) as source:
                        assert source.extractfile('backup/code-server-home/original').read() == b'original\n'
                    script += 'test -n "$(find /data -maxdepth 1 -name ".restore-staging-*")"'
                subprocess.run([docker, 'run', '--rm', '--network', 'none',
                                '--mount', f'type=volume,src={volume},dst=/data,readonly',
                                '--entrypoint', '/bin/sh', image, '-ec', script], check=True)
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=30)
                subprocess.run([docker, 'rm', '-f', guard], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run([docker, 'volume', 'rm', volume], check=True, stdout=subprocess.DEVNULL)
    print('PASS: Exclusive restore reservation, SIGTERM cleanup, and rollback guidance.')


if __name__ == '__main__':
    run(sys.argv[1])
