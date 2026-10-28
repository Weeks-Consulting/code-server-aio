#!/bin/sh
# Stream an archive into volume-local staging and preserve a verified rollback.
set -eu
command -v python3 >/dev/null || { echo 'Install python3 to restore backups.' >&2; exit 1; }
exec python3 - "$@" <<'PYTHON'
import os
import hashlib
from pathlib import Path
import shutil
import signal
import shlex
import subprocess
import sys
import tempfile
import uuid


def interrupted(signum, frame):
    raise SystemExit(128 + signum)


for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(signum, interrupted)


def run(*arguments, **kwargs):
    return subprocess.run(arguments, check=True, **kwargs)


if len(sys.argv) != 3:
    sys.exit('Usage: sh restore-home.sh ARCHIVE.tar.gz[.gpg] EXISTING_VOLUME')
archive = Path(sys.argv[1]).resolve()
volume = sys.argv[2]
if not archive.is_file():
    sys.exit('Archive does not exist or is not a regular file: ' + str(archive))
if ',' in str(archive) or ',' in volume:
    sys.exit('Archive paths and volume names must not contain commas')
key = os.environ.get('BACKUP_GPG_PASSPHRASE', '')
encrypted = archive.name.endswith('.gpg')
if encrypted and not key:
    sys.exit('Encrypted restore requires the original BACKUP_GPG_PASSPHRASE (manage reads it from .env).')
if key and not shutil.which('gpg'):
    sys.exit('Install gpg on the Docker host to restore encrypted backups or encrypt rollback backups.')
run('docker', 'volume', 'inspect', volume, stdout=subprocess.DEVNULL)
if subprocess.check_output(['docker', 'ps', '-q', '--filter', 'volume=' + volume]).strip():
    sys.exit('Stop all containers using ' + volume + ' before restoring (use docker compose down).')
parent = archive.parent
if archive.name in ('original-home.tar.gz', 'original-home.tar.gz.gpg'):
    import re
    while re.fullmatch(r'pre-restore\.[A-Za-z0-9_]{8}', parent.name):
        parent = parent.parent
rollback = Path(tempfile.mkdtemp(prefix='pre-restore.', dir=parent))
os.chmod(rollback, 0o700)
name = 'original-home.tar.gz' + ('.gpg' if key else '')
partial = rollback / ('.' + name + '.partial')
stage = '.restore-staging-' + uuid.uuid4().hex
image = os.environ.get('RESTORE_IMAGE', 'debian:13-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad')
guard = 'code-server-restore-' + hashlib.sha256(volume.encode()).hexdigest()[:24]
create = ['docker', 'create', '--name', guard, '--network', 'none', '--user', '0:0',
        '--mount', 'type=volume,src=' + volume + ',dst=/restore',
        '--env', 'STAGE=' + stage, '--entrypoint', '/bin/sh', image, '-c', 'exec sleep infinity']
base = ['docker', 'exec', '-i', guard, '/bin/sh', '-euc']
processes = []
acquired = False
replacement_started = False
restored = False


def spawn(arguments, **kwargs):
    process = subprocess.Popen(arguments, **kwargs)
    processes.append(process)
    return process


def helper(script, **kwargs):
    return spawn(base + [script], **kwargs)


# Keys travel over an inherited pipe, never argv or a plaintext disk file.
def gpg(arguments, **kwargs):
    reader, writer = os.pipe()
    try:
        os.write(writer, (key + '\n').encode())
    finally:
        os.close(writer)
    try:
        return spawn(['gpg', '--homedir', gpg_home, '--batch', '--yes',
                                 '--pinentry-mode', 'loopback', '--passphrase-fd', str(reader),
                                 *arguments], pass_fds=(reader,), **kwargs)
    finally:
        os.close(reader)


def finish(processes):
    statuses = [process.wait() for process in processes]
    if any(statuses):
        raise RuntimeError('Restore stream failed (exit codes: ' + str(statuses) + ')')


try:
    # Docker reserves names atomically across clients of this daemon. Keep one
    # container alive for the entire restore, rather than checking between helpers.
    reservation = subprocess.run(create, capture_output=True, text=True)
    if reservation.returncode:
        raise RuntimeError(f'Could not reserve restore container {guard}: {reservation.stderr.strip()}')
    acquired = True
    run('docker', 'start', guard, stdout=subprocess.DEVNULL)
    own_id = reservation.stdout.strip()
    attached = subprocess.check_output(['docker', 'ps', '-q', '--no-trunc', '--filter', 'volume=' + volume]).decode().split()
    if any(container != own_id for container in attached):
        raise RuntimeError('Another container is using the target volume; keep the stack stopped during restore.')
    # GPG's agent creates Unix sockets here. Shared Docker TMPDIR paths (act,
    # Docker Desktop) can exceed their length limit; this directory is host-only.
    with tempfile.TemporaryDirectory(prefix='restore-gpg-', dir='/tmp') as gpg_home:
        # GNU tar rejects traversal and directory symlink attacks. The entire
        # archive is isolated under a new directory until layout validation.
        extract = '''
            umask 077
            mkdir -- "/restore/$STAGE"
            tar -xzp --numeric-owner --same-owner -f - -C "/restore/$STAGE"
            root="/restore/$STAGE"
            test -d "$root/backup" && test ! -L "$root/backup"
            test -d "$root/backup/code-server-home" && test ! -L "$root/backup/code-server-home"
            test "$(find "$root" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1
            test "$(find "$root/backup" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1
        '''
        if encrypted:
            decrypt = gpg(['--decrypt', str(archive)], stdout=subprocess.PIPE)
            try:
                extraction = helper(extract, stdin=decrypt.stdout)
            except BaseException:
                decrypt.stdout.close()
                decrypt.wait()
                raise
            decrypt.stdout.close()
            finish([extraction, decrypt])
        else:
            with archive.open('rb') as source:
                finish([helper(extract, stdin=source)])
        # Exclude staging from the rollback while preserving scheduled-backup layout.
        backup_script = '''
            tar -czp --numeric-owner --exclude="./$STAGE" \
                --transform="flags=rh;s,^\\.,backup/code-server-home," -f - -C /restore .
        '''
        with partial.open('xb') as output:
            os.chmod(partial, 0o600)
            backup = helper(backup_script, stdout=subprocess.PIPE)
            if key:
                encryption = gpg(['--symmetric', '--cipher-algo', 'AES256'],
                                 stdin=backup.stdout, stdout=output)
                backup.stdout.close()
                finish([encryption, backup])
            else:
                shutil.copyfileobj(backup.stdout, output, length=1024 * 1024)
                backup.stdout.close()
                finish([backup])
        # Verify the completed rollback before modifying the original entries.
        verify_script = 'tar -tzf - >/dev/null'
        if key:
            decrypt = gpg(['--decrypt', str(partial)], stdout=subprocess.PIPE)
            verification = helper(verify_script, stdin=decrypt.stdout)
            decrypt.stdout.close()
            finish([verification, decrypt])
        else:
            with partial.open('rb') as source:
                finish([helper(verify_script, stdin=source)])
        partial.rename(rollback / name)
        print('Original volume backup: ' + str(rollback / name), flush=True)
        print('Original volume backup complete; replacing volume contents.', flush=True)
        replacement_started = True
        finish([helper('''
            find /restore -mindepth 1 -maxdepth 1 ! -name "$STAGE" -exec rm -rf -- {} +
            source="/restore/$STAGE/backup/code-server-home"
            # Preserve metadata of the home volume root as well as its entries.
            chown --reference="$source" /restore
            chmod --reference="$source" /restore
            find "$source" -mindepth 1 -maxdepth 1 -exec mv -t /restore -- {} +
            touch -r "$source" /restore
        ''')])
        restored = True
    print('Restored ' + volume + '. Restart the deployment with its usual launch options.')
except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
    sys.exit('Restore failed: ' + str(error))
finally:
    if acquired:
        # Kill the container first: terminating a Docker CLI does not guarantee
        # that its remote extraction/replacement process has stopped.
        stopped = subprocess.run(['docker', 'kill', guard], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL).returncode == 0
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if stopped and (not replacement_started or restored):
            subprocess.run(['docker', 'start', guard], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(base + ['rm -rf -- "/restore/$STAGE"'], check=True)
        elif replacement_started and not restored:
            print(f'Replacement interrupted; staged files remain at /{stage} in {volume}.', file=sys.stderr)
        if stopped:
            subprocess.run(['docker', 'rm', '-f', guard], check=True, stdout=subprocess.DEVNULL)
        else:
            print(f'Could not stop restore container {guard}; inspect it before removing staging or restarting.', file=sys.stderr)
    if replacement_started and not restored and (rollback / name).exists():
        print(f'Keep the stack stopped. Recover with: manage restore --archive '
              f'{shlex.quote(str(rollback / name))} --volume {shlex.quote(volume)}', file=sys.stderr)
    partial.unlink(missing_ok=True)
    try:
        rollback.rmdir()
    except OSError:
        pass
PYTHON
