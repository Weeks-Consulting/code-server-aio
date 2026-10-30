"""Check real launcher serialization with manual and scheduled sidecar backups."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]


def wait_for(predicate, description, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError('Timed out waiting for ' + description)


def integration(image, mode, management_source=ROOT / 'manage.py'):
    project = f'backup-lock-{os.getpid()}-{mode}'
    app = project + '-app'
    processes = []
    # Keep the configuration mountable by Docker Desktop and act siblings.
    with tempfile.TemporaryDirectory(prefix='.test-tmp.', dir=os.environ.get('TEST_TMPDIR', ROOT)) as temporary:
        directory = Path(temporary).resolve()
        for name in ('manage', 'compose.yaml', 'compose.backup-common.yaml', 'compose.backup-local.yaml'):
            shutil.copyfile(ROOT / name, directory / name)
        shutil.copyfile(management_source, directory / 'manage.py')
        shutil.copyfile(ROOT / 'build/tools/backup-key.py', directory / 'backup-key.py')
        (directory / '.env').write_text(
            'CF_TUNNEL_TOKEN=test-only\n'
            'BACKUP_GPG_PASSPHRASE=disposable-backup-lock-key\n'
            'BACKUP_LOCAL_PATH=./archives\n'
            'BACKUP_TIMEZONE=Etc/UTC\n'
            'BACKUP_RETENTION_DAYS=-1\n')
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith(('BACKUP_', 'CF_', 'COMPOSE_', 'CODE_SERVER_'))}
        environment.update(BACKUP_LOCAL_UID=str(os.getuid()), BACKUP_LOCAL_GID=str(os.getgid()))
        compose = ['docker', 'compose', '--project-directory', str(directory), '--project-name', project,
                   '--env-file', str(directory / '.env'), '-f', str(directory / 'compose.yaml'),
                   '-f', str(directory / 'compose.backup-common.yaml'),
                   '-f', str(directory / 'compose.backup-local.yaml')]
        launcher = ['sh', str(directory / 'manage'), 'backup', '--backup', 'local', '--project-name', project]

        def docker(*arguments):
            return subprocess.check_output(['docker', *arguments], text=True, env=environment).strip()

        def start_manual(log):
            process = subprocess.Popen(launcher, env=environment, stdout=log, stderr=subprocess.STDOUT)
            processes.append(process)
            return process

        def hook_count():
            # archive-pre runs while the application is still running. Each
            # invocation records a distinct PID before blocking on release.
            return int(docker('exec', app, '/bin/sh', '-ec',
                              'find /tmp/backup-hooks -type f | wc -l'))

        first_log = directory / 'first.log'
        second_log = directory / 'second.log'
        first = None
        try:
            volume = project + '_code-server-home'
            docker('volume', 'create', '--label', f'com.docker.compose.project={project}',
                   '--label', 'com.docker.compose.volume=code-server-home', volume)
            hook = "/bin/sh -ec 'touch /tmp/backup-hooks/$$; while [ ! -f /tmp/release-backup ]; do sleep 0.1; done'"
            docker('run', '-d', '--init', '--name', app, '--network', 'none',
                   '--label', f'docker-volume-backup.stop-during-backup={project}-home',
                   '--label', f'docker-volume-backup.exec-label={project}-local-home',
                   '--label', 'docker-volume-backup.archive-pre=' + hook,
                   '--mount', f'type=volume,src={volume},dst=/home',
                   '--entrypoint', '/bin/sleep', image, 'infinity')
            docker('exec', app, '/bin/sh', '-ec', 'mkdir /tmp/backup-hooks; echo snapshot > /home/sentinel')
            started_before = docker('inspect', '-f', '{{.State.StartedAt}}', app)
            # A six-field expression schedules exactly one run this year. Avoid
            # recurring jobs piling up behind the intentionally held lock.
            scheduled = datetime.now(timezone.utc) + timedelta(seconds=15)
            expression = scheduled.strftime('%S %M %H %d %m *') if mode == 'scheduled' else '0 0 31 2 *'
            with (directory / '.env').open('a') as output:
                output.write(f'BACKUP_CRON_EXPRESSION="{expression}"\n')
            subprocess.run([*compose, 'up', '-d', '--no-deps', 'backup'], env=environment, check=True)
            with first_log.open('w') as output:
                if mode == 'manual':
                    first = start_manual(output)
                wait_for(lambda: hook_count() == 1, 'first archive hook')
                # Isolate locking from the separate second-resolution filename
                # collision: Offen records its start time before waiting on the
                # lock, so queued runs can otherwise overwrite the same archive.
                time.sleep(1.1)
                with second_log.open('w') as second_output:
                    second = start_manual(second_output)

                    def waiting_or_overlapping():
                        return ('Exclusive lock was not available' in second_log.read_text()
                                or hook_count() > 1 or second.poll() is not None)

                    wait_for(waiting_or_overlapping, 'second backup to wait for the shared lock')
                    assert hook_count() == 1, 'Second backup bypassed the first backup lock'
                    assert second.poll() is None, second_log.read_text()
                    assert 'Exclusive lock was not available' in second_log.read_text(), second_log.read_text()
                    docker('exec', app, 'touch', '/tmp/release-backup')
                    if first is not None:
                        assert first.wait(timeout=45) == 0, first_log.read_text()
                    assert second.wait(timeout=45) == 0, second_log.read_text()
            assert hook_count() == 2, 'Expected two serialized archive operations'
            assert 'Acquired exclusive lock on subsequent attempt' in second_log.read_text()
            assert docker('inspect', '-f', '{{.State.Running}}', app) == 'true'
            assert docker('inspect', '-f', '{{.State.StartedAt}}', app) != started_before
            assert len(list((directory / 'archives').glob('*.tar.gz.gpg'))) == 2
            # A missing sidecar must fail instead of creating a fresh lock domain.
            subprocess.run([*compose, 'stop', 'backup'], env=environment, check=True)
            missing = subprocess.run(launcher, env=environment, capture_output=True, text=True, timeout=15)
            assert missing.returncode != 0, 'Backup succeeded without its running sidecar'
            assert len(list((directory / 'archives').glob('*.tar.gz.gpg'))) == 2
            print(f'PASS: {mode}/manual backup serialization, application restart, and stopped-sidecar rejection.')
        except BaseException:
            for path in (first_log, second_log):
                if path.exists():
                    print(path.read_text(), file=sys.stderr)
            subprocess.run([*compose, 'logs', '--no-color', 'backup'], env=environment)
            raise
        finally:
            # Stop remote backup processes before deleting their fixtures. A CLI
            # interruption alone does not stop a Docker exec or one-off container.
            containers = docker('ps', '-aq', '--filter', f'label=com.docker.compose.project={project}').split()
            if containers:
                subprocess.run(['docker', 'rm', '-f', *containers], env=environment,
                               stdout=subprocess.DEVNULL)
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            subprocess.run(['docker', 'rm', '-f', app], env=environment,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([*compose, 'down', '--volumes', '--remove-orphans'], env=environment, check=True,
                           stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    for mode in ('manual', 'scheduled'):
        integration(sys.argv[1], mode)
