#!/bin/sh
# Stream an archive into volume-local staging and preserve a verified rollback.
set -eu
command -v python3 >/dev/null || { echo 'Install python3 to restore backups.' >&2; exit 1; }
exec python3 - "$@" <<'PYTHON'
import os
import hashlib
import json
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
if ',' in volume:
    sys.exit('Volume names must not contain commas')
key = os.environ.get('BACKUP_GPG_PASSPHRASE', '')
encrypted = archive.name.endswith('.gpg')
if encrypted and not key:
    sys.exit('Encrypted restore requires the original BACKUP_GPG_PASSPHRASE (manage reads it from .env).')
if key and not shutil.which('gpg'):
    sys.exit('Install gpg on the Docker host to restore encrypted backups or encrypt rollback backups.')
try:
    run('docker', 'volume', 'inspect', volume, stdout=subprocess.DEVNULL)
    if subprocess.check_output(['docker', 'ps', '-q', '--filter', 'volume=' + volume]).strip():
        sys.exit('Stop all containers using ' + volume + ' before restoring (use docker compose down).')
except (OSError, subprocess.CalledProcessError) as error:
    sys.exit('Cannot inspect restore volume; check its name and Docker access: ' + str(error))
parent = archive.parent
if archive.name in ('original-home.tar.gz', 'original-home.tar.gz.gpg'):
    import re
    while re.fullmatch(r'pre-restore\.[A-Za-z0-9_]{8}', parent.name):
        parent = parent.parent
try:
    rollback = Path(tempfile.mkdtemp(prefix='pre-restore.', dir=parent))
except OSError as error:
    sys.exit('Cannot create rollback directory beside archive; copy the archive to a writable folder: ' + str(error))
os.chmod(rollback, 0o700)
name = 'original-home.tar.gz' + ('.gpg' if key else '')
partial = rollback / ('.' + name + '.partial')
stage = '.restore-staging-' + uuid.uuid4().hex
image = os.environ.get('RESTORE_IMAGE', 'debian:13-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4')
guard = 'code-server-restore-' + hashlib.sha256(volume.encode()).hexdigest()[:24]
create = ['docker', 'create', '--name', guard, '--network', 'none', '--user', '0:0',
        '--label', 'code-server.restore-owner=' + stage,
        '--mount', 'type=volume,src=' + volume + ',dst=/restore',
        '--env', 'STAGE=' + stage, '--entrypoint', '/bin/sh', image, '-c', 'exec sleep infinity']
base = ['docker', 'exec', '-i', guard, '/bin/sh', '-euc']
processes = []
acquired = False
create_attempted = False
start_attempted = False
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
    create_attempted = True
    reservation = subprocess.run(create, capture_output=True, text=True)
    if reservation.returncode:
        raise RuntimeError(f'Could not reserve restore container {guard}: {reservation.stderr.strip()}')
    own_id = reservation.stdout.strip()
    acquired = True
    start_attempted = True
    run('docker', 'start', guard, stdout=subprocess.DEVNULL)
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
    # A repeated interrupt must not abandon cleanup midway; each Docker call is bounded.
    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, signal.SIG_IGN)
    # Give recovery guidance before any cleanup call that could stall.
    if replacement_started and not restored and (rollback / name).exists():
        print(f'Keep the stack stopped. Recover with: manage restore --archive '
              f'{shlex.quote(str(rollback / name))} --volume {shlex.quote(volume)}', file=sys.stderr)

    def cleanup(*arguments):
        try:
            return subprocess.run(arguments, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=60).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    incomplete = False
    if create_attempted and not start_attempted:
        # Cancellation can kill the CLI after the daemon has created the guard.
        # Recover only our invocation's reservation, never a competing restore's.
        candidate = own_id if acquired else None
        if not acquired:
            try:
                inspection = subprocess.run(['docker', 'inspect', '--format', '{{json .}}', guard],
                                            capture_output=True, text=True, check=True, timeout=60)
                info = json.loads(inspection.stdout)
                if (info['Config'].get('Labels') or {}).get('code-server.restore-owner') == stage:
                    candidate = info['Id']
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                print(f'Could not resolve restore reservation {guard}; inspect it before '
                      'removing it or retrying the restore.', file=sys.stderr)
        # No helper has started, so there is no staging to clean. Use the ID to
        # avoid deleting a replacement reservation if the name changes hands.
        if candidate and not cleanup('docker', 'rm', '-f', candidate):
            print(f'Could not remove restore container {guard}; inspect it before '
                  'retrying the restore.', file=sys.stderr)
    elif acquired:
        # Kill the container first: terminating a Docker CLI does not guarantee
        # that its remote extraction/replacement process has stopped.
        stopped = cleanup('docker', 'kill', guard)
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if stopped and (not replacement_started or restored):
            if not (cleanup('docker', 'start', guard) and cleanup(*base, 'rm -rf -- "/restore/$STAGE"')):
                incomplete = True
                print(f'Could not remove staging /{stage} in {volume}; remove it before restarting.', file=sys.stderr)
        elif replacement_started and not restored:
            print(f'Replacement interrupted; staged files remain at /{stage} in {volume}.', file=sys.stderr)
        if not stopped:
            incomplete = True
            print(f'Could not stop restore container {guard}; inspect it before removing staging or restarting.', file=sys.stderr)
        elif not cleanup('docker', 'rm', '-f', guard):
            incomplete = True
            print(f'Could not remove restore container {guard}; remove it before another restore.', file=sys.stderr)
    partial.unlink(missing_ok=True)
    try:
        rollback.rmdir()
    except OSError:
        pass
    # Failed paths already exit nonzero; a completed restore still reports cleanup failure.
    if restored and incomplete:
        sys.exit(1)
PYTHON
