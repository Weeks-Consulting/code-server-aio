#!/usr/bin/env python3
"""Install and manage a published Compose deployment using Python's standard library."""

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


# Matches CI's release grammar: an optional revision is at least 1.
TAG = r'v\d+\.\d+\.\d+(?:-[1-9]\d*)?'
DEFAULT_IMAGE = "ghcr.io/weeks-consulting/code-server-aio:latest"
BUNDLE = "code-server-aio.tar.gz"
IMAGE_SETTING = re.compile(r"^[ \t]*(?:export[ \t]+)?CODE_SERVER_IMAGE[ \t]*[=:](.*)$", re.M)


def fail(message):
    raise ValueError(message)


def helper(directory, name):
    for path in (directory / name, directory / "build/tools" / name):
        if path.is_file():
            return path
    fail(f"{name} helper is missing; reinstall this release.")


def validate_dotenv(text):
    """Require quoted dotenv values to end on the same physical line."""
    assignment = re.compile(r"^[ \t]*(?:export[ \t]+)?[A-Za-z_][A-Za-z0-9_]*[ \t]*[=:][ \t]*(.*)$")
    for number, line in enumerate(text.splitlines(), 1):
        match = assignment.match(line)
        if not match or not match[1].startswith(("'", '"')):
            continue
        value = match[1]
        escaped = False
        for character in value[1:]:
            if escaped:
                escaped = False
            elif character == "\\":
                # Compose's dotenv parser also treats backslash as an escape in single quotes.
                escaped = True
            elif character == value[0]:
                break
        else:
            raise ValueError(f"Multiline or unterminated quoted .env value at line {number}; use a single-line value.")


def image_repository(original):
    validate_dotenv(original)
    matches = list(IMAGE_SETTING.finditer(original))
    image = DEFAULT_IMAGE
    if matches:
        value = matches[-1].group(1).strip()
        if value.startswith(("\"", "'")):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].strip().startswith("#")):
                raise ValueError("Invalid CODE_SERVER_IMAGE quoting")
            image = value[1:end]
        else:
            image = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    image = image or DEFAULT_IMAGE
    match = re.fullmatch(r"ghcr\.io/([a-z0-9_.-]+/[a-z0-9_.-]+)(?::[A-Za-z0-9_.-]+)?(?:@sha256:[a-f0-9]{64})?", image)
    if not match:
        raise ValueError("CODE_SERVER_IMAGE must name a ghcr.io/owner/repo image")
    return match[1]


def stable_tag(release):
    tag = release["tag_name"]
    if release.get("draft") or release.get("prerelease") or not re.fullmatch(TAG, tag):
        fail("GitHub returned an invalid stable release tag")
    return tag


def latest_tag(repository, timeout=15):
    request = urllib.request.Request(
        "https://api.github.com/repos/" + repository + "/releases/latest",
        headers={"User-Agent": "code-server-aio-manage", "Accept": "application/vnd.github+json"})
    with release_opener().open(request, timeout=timeout) as response:
        release = json.load(response)
    return stable_tag(release)


def published_release(repository, version=None):
    """Resolve a published release and the repository's canonical spelling."""
    if version and not re.fullmatch(TAG, version):
        fail(f'Expected a release tag such as v4.141.0 or v4.141.0-1; got: {version}')
    endpoint = f'tags/{version}' if version else 'latest'
    release = json.loads(download(f'https://api.github.com/repos/{repository}/releases/{endpoint}'))
    if version:
        tag = release['tag_name']
        if release.get('draft') or tag != version:
            fail('GitHub returned an invalid published release tag')
    else:
        tag = stable_tag(release)
    # Release assets upload only after the images are tested and published.
    if not {BUNDLE, 'SHA256SUMS'} <= {asset.get('name') for asset in release.get('assets', [])}:
        fail(f'Release {tag} is still publishing: its deployment bundle is not available yet; try again later.')
    # GHCR names are lowercase; download from GitHub's own spelling of the repository.
    match = re.match(r'https://github\.com/([^/]+/[^/]+)/releases/', release.get('html_url', ''))
    canonical = match[1] if match else repository
    if canonical.lower() != repository.lower():
        fail(f'GitHub returned release {tag} for {canonical}, not {repository}')
    return tag, canonical


@contextmanager
def env_lock(path):
    # A stable sidecar keeps installers and key/image updates on the same inode.
    descriptor = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as guard:
        if path.exists():
            owner, current = path.stat(), os.fstat(guard.fileno())
            if (owner.st_uid, owner.st_gid) != (current.st_uid, current.st_gid):
                os.fchown(guard.fileno(), owner.st_uid, owner.st_gid)
        fcntl.flock(guard, fcntl.LOCK_EX)
        yield guard.fileno()


def upgrade(directory, version=None):
    """Install and start the selected release."""
    path = directory / ".env"
    try:
        # A checkout's deployment files are tracked source; never overwrite them.
        if (directory / "build/tools/install.sh").is_file():
            raise ValueError("upgrade refreshes installed deployments. In a source checkout, check out "
                             "the release tag with git and set CODE_SERVER_IMAGE in .env.")
        if path.is_symlink() or not path.is_file():
            raise ValueError("Expected a regular .env file")
        repository = image_repository(path.read_text())
        version, repository = published_release(repository, version)
        install(argparse.Namespace(directory=str(directory), repository=repository,
                                   version=version, dry_run=False), upgrade=True)
    except Exception as error:
        sys.exit("Unable to upgrade: " + str(error))


def parser():
    result = argparse.ArgumentParser(
        prog='manage', usage='%(prog)s COMMAND [OPTIONS]',
        # Abbreviations would let the bootstrap and manage read options differently.
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Install and manage a code-server deployment.\n'
                    'Commands and options may appear in any order. A command is required.\n\n'
                    'commands (brackets mark optional arguments):\n'
                    '  configure [--backup {none,s3,local}] [--project-name NAME] [--pull] [--start]\n'
                    '      Create/update containers and save configuration; --start starts them afterward.\n'
                    '  start [--pull]\n'
                    '      Start configured containers; --pull refreshes images and recreates as needed.\n'
                    '  stop\n'
                    '      Stop containers; keep containers, networks, and volumes.\n'
                    '  logs [-f|--follow]\n'
                    '      Print logs from the saved deployment; add -f to follow.\n'
                    '  upgrade [--version TAG]\n'
                    '      Install and start a selected release (default: latest stable).\n'
                    '  backup --run | --list | --restore --archive FILE_OR_URI [--volume NAME]\n'
                    '      Run a backup, list configured archives, or restore an existing home volume.\n'
                    '  install [--directory PATH] [--repository OWNER/REPO] [--version TAG] [--dry-run]\n'
                    '      Install or refresh deployment files; no services started.\n'
                    '  All commands accept -h or --help.',
        epilog='examples:\n'
               '  manage configure --pull --start                Configure and start\n'
               '  manage configure --backup local --pull --start  Enable local backups and start\n'
               '  manage stop                                    Stop existing containers\n'
               '  manage start                                   Resume existing containers\n'
               '  manage logs -f                                 Follow service logs\n'
               '  manage backup --run                            Run a backup now\n'
               '  manage backup --list                           List configured archives\n'
               '  manage backup --restore --archive FILE_OR_URI\n\n'
               'configure reads .env and Compose files beside manage and saves a private configuration.\n'
               'Later commands use that saved configuration, including backup mode and project name.\n'
               'Run configure again to apply changes. If installed with sudo, run with sudo.')
    result.add_argument('action', metavar='COMMAND',
                        choices=['install', 'configure', 'start', 'stop', 'logs', 'upgrade', 'backup'],
                        help=argparse.SUPPRESS)
    configure = result.add_argument_group('configure options')
    configure.add_argument('--backup', choices=['none', 's3', 'local'],
                           help='Backup services to include (default: saved mode, or none on first configure)')
    configure.add_argument('--project-name', metavar='NAME',
                           help='Compose project name (default: saved name, or Compose default). '
                                'Changing it requires no running containers or volumes for the saved project.')
    configure.add_argument('--start', action='store_true', help='Start containers after configuring them')
    logs = result.add_argument_group('logs options')
    logs.add_argument('-f', '--follow', action='store_true', help='Keep streaming new log output')
    backup = result.add_argument_group('backup options (choose exactly one action)')
    actions = backup.add_mutually_exclusive_group()
    actions.add_argument('--run', dest='backup_action', action='store_const', const='run',
                         help='Run a backup inside the configured, running backup service')
    actions.add_argument('--list', dest='backup_action', action='store_const', const='list',
                         help='List archives from the configured local or S3 backup destination')
    actions.add_argument('--restore', dest='backup_action', action='store_const', const='restore',
                         help='Restore an existing home volume; requires --archive')
    backup.add_argument('--archive', metavar='FILE_OR_URI',
                        help='Local path or s3:// URI for --restore (.tar.gz or .tar.gz.gpg)')
    backup.add_argument('--volume', metavar='NAME',
                        help='Override the saved Docker home volume for --restore')
    install = result.add_argument_group('install options')
    install.add_argument('--directory', metavar='PATH', help='Installation directory (default: ~/code-server, or /opt/code-server as root)')
    install.add_argument('--repository', metavar='OWNER/REPO', help='GitHub repository (default: Weeks-Consulting/code-server-aio)')
    install.add_argument('--dry-run', action='store_true', help='Preview installation without changes or network requests')
    release = result.add_argument_group('release selection (install and upgrade)')
    release.add_argument('--version', metavar='TAG', help='Release tag vX.Y.Z[-N], including prereleases (default: latest stable)')
    images = result.add_argument_group('image refresh (configure and start)')
    images.add_argument('--pull', action='store_true', help='Refresh all configured images and recreate containers as needed '
                        '(configure always pulls images that are missing locally)')
    return result


CONFIGURATION = '.manage.compose.json'


def configuration_path(directory):
    path = directory / CONFIGURATION
    if path.is_symlink():
        fail('Expected a regular saved configuration file: ' + str(path))
    return path


def saved_configuration(directory):
    path = configuration_path(directory)
    if not path.is_file():
        fail('No saved configuration. Run manage configure --start first; add --backup local or s3 if needed.')
    return json.loads(path.read_text())


def compose_command(directory, path):
    # The snapshot is already interpolated; do not read changing deployment .env values.
    command = ['docker', 'compose', '--project-directory', str(directory),
               '--env-file', '/dev/null', '-f', str(path)]
    project = json.loads(Path(path).read_text()).get('name')
    if project:
        # An exported COMPOSE_PROJECT_NAME must not override the saved deployment.
        command += ['--project-name', project]
    return command


@contextmanager
def reserve_backup(model):
    project = model.get('name')
    if not project or not model.get('volumes'):
        yield
        return
    sidecars = subprocess.check_output([
        'docker', 'container', 'ls', '--quiet',
        '--filter', f'label=com.docker.compose.project={project}',
        '--filter', 'label=com.docker.compose.service=backup',
        # `compose run` one-off containers (backup --list) are not the sidecar.
        '--filter', 'label=com.docker.compose.oneoff=False',
        # A restarting (crash-looping) sidecar cannot be running a backup, and
        # Docker refuses exec into it; it must not block stop or reconfiguration.
        '--filter', 'status=running',
    ], text=True).split()
    if not sidecars:
        yield
        return
    if len(sidecars) != 1:
        fail(f'Expected one running backup container for {project}. Inspect the backup containers and retry.')
    # Scheduled and manual Offen runs take this same lock before stopping the
    # workspace. Keep the Docker exec input open to hold it during the operation.
    command = ['docker', 'exec', '-i', sidecars[0], 'flock', '-n',
               '/var/lock/dockervolumebackup.lock', '/bin/sh', '-ec', 'echo locked; cat >/dev/null']
    # Its own session keeps a terminal Ctrl-C from releasing the lock while a
    # forwarded signal lets Compose finish; closing stdin releases it.
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, start_new_session=True) as lock:
        try:
            if lock.stdout.readline() == 'locked\n':
                yield
                return
            lock.stdin.close()
            error = lock.stderr.read().strip()
            lock.wait()
            # The sidecar may have stopped or begun restarting since it was listed.
            state = subprocess.run(['docker', 'inspect', '--format', '{{.State.Status}}', sidecars[0]],
                                   capture_output=True, text=True).stdout.strip()
            if state != 'running':
                print(f'Backup service for {project} is {state or "gone"}; continuing without its backup lock.',
                      file=sys.stderr)
                yield
                return
            if lock.returncode == 1 and not error:
                fail(f'Could not acquire the backup lock for {project}: a backup is running. '
                     'Wait for it to finish and retry.')
            fail(f'Could not acquire the backup lock for {project} (backup container {sidecars[0]} is {state}): '
                 f'{error or "exit status " + str(lock.returncode)}')
        finally:
            if not lock.stdin.closed:
                lock.stdin.close()


def start_workspace(run, command, model):
    # A newly started sidecar can run immediately. Start the workspace before
    # enabling its backup schedule, including with older saved Compose models.
    services = model['services']
    if 'backup' not in services:
        run(command + ['start'])
    else:
        run(command + ['start', *(service for service in services if service != 'backup')])
        run(command + ['start', 'backup'])
    tag_sidecar_images(command, services)


def image_present(image):
    return subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def service_image_id(command, service):
    containers = subprocess.check_output(command + ['ps', '--all', '--quiet', service], text=True).split()
    if containers:
        return subprocess.check_output(
            ['docker', 'inspect', '--format', '{{.Image}}', containers[0]], text=True).strip()


def tag_sidecar_images(command, services):
    for service in ('cloudflared', 'backup'):
        reference = services.get(service, {}).get('image', '')
        name, separator, _ = reference.partition('@')
        if not separator:
            continue
        repository = re.sub(r':[^/:]+$', '', name)
        try:
            image_id = service_image_id(command, service)
            if not image_id:
                continue
            created = subprocess.check_output(
                ['docker', 'image', 'inspect', '--format', '{{.Created}}', image_id], text=True)
            date = re.match(r'(\d{4}-\d{2}-\d{2})T', created)
            stamp = date[1] if date else 'pinned'
            short_id = image_id.rsplit(':', 1)[-1][:12]
            subprocess.run(['docker', 'image', 'tag', image_id, f'{repository}:{stamp}-{short_id}'], check=True)
        except (OSError, subprocess.CalledProcessError) as error:
            print(f'Warning: could not tag the {service} image: {error}', file=sys.stderr)


def remove_superseded_image(image_id, old_reference, command):
    try:
        current_id = service_image_id(command, 'code-server')
        if not current_id or image_id == current_id:
            return
        tags = json.loads(subprocess.check_output(
            ['docker', 'image', 'inspect', '--format', '{{json .RepoTags}}', image_id], text=True))
        # Removing by ID can also remove a tag, so protect any other local names.
        if tags and tags != [old_reference]:
            return
        subprocess.run(['docker', 'image', 'rm', '--no-prune', image_id], check=True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'Warning: could not remove previous workspace image {image_id}: {error}', file=sys.stderr)


def pull_images(command, model, refresh):
    """Pull with visible progress before taking any reservation or backup lock."""
    services = [name for name, service in model['services'].items()
                if service.get('image') and not service.get('build')
                and (refresh or not image_present(service['image']))]
    if services:
        # Pulling changes no containers, so it needs no lifecycle lock.
        subprocess.run(command + ['pull', *services], check=True)


def run_checked(arguments):
    subprocess.run(arguments, check=True)


def find_owned_reservation(name, owner):
    try:
        result = subprocess.run(['docker', 'inspect', '--type', 'container', '--format', '{{json .}}', name],
                                capture_output=True, text=True, check=True, timeout=15, start_new_session=True)
        container = json.loads(result.stdout)
        if (container['Config'].get('Labels') or {}).get('code-server.lifecycle-owner') == owner:
            return container['Id']
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        # A cancelled create may reach the daemon after this inspection.
        print(f'Could not verify reservation {name} (owner {owner}). '
              'Inspect it before removing it or retrying.', file=sys.stderr)
    return None


# After forwarding a signal, wait this long for Compose to exit on its own.
LIFECYCLE_STOP_TIMEOUT = 60


def home_volume(model, default_key=None):
    workspace = model.get('services', {}).get('code-server', {})
    volume_key = next((mount['source'] for mount in workspace.get('volumes', [])
                       if mount['type'] == 'volume' and mount['target'] == '/home'), default_key)
    return model.get('volumes', {}).get(volume_key)


@contextmanager
def reserve_workspace(model, command, use_saved_image):
    """Yield a runner for lifecycle commands while holding the volume reservation."""
    workspace = model['services']['code-server']
    volume = home_volume(model, default_key='code-server-home')
    if volume is None:
        yield run_checked
        return
    # Share the restore helper's atomic Docker name reservation, even across
    # clients of a remote daemon. A stopped guard need not mount the volume.
    name = 'code-server-restore-' + hashlib.sha256(volume['name'].encode()).hexdigest()[:24]
    if use_saved_image:
        image = workspace['image']
    else:
        # Use the existing container's image ID so plain start works offline
        # even if a local tag has since been moved or removed.
        image = service_image_id(command, 'code-server')
        if not image:
            fail('No workspace container exists. Run manage configure --start to recreate the deployment.')
    owner = uuid.uuid4().hex
    handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)}
    received_signal = None
    acquiring = False
    create_returned = False
    changing = False
    cleaning_up = False
    guard = None
    child = None
    # True while a lifecycle Compose client may still be acting on Docker.
    unfinished = False
    abandoned = False

    def interrupt(signum, frame):
        nonlocal received_signal, abandoned
        if child is not None and child.poll() is None:
            # Let Compose stop cleanly; a second signal abandons the wait.
            if received_signal is None:
                received_signal = signum
                child.send_signal(signum)
            else:
                abandoned = True
            return
        if received_signal is None:
            received_signal = signum
            if not cleaning_up:
                raise SystemExit(128 + signum)

    def run(arguments):
        nonlocal child, unfinished, abandoned
        if received_signal is not None:
            raise SystemExit(128 + received_signal)
        unfinished = True
        # A separate session receives only forwarded signals, exactly once.
        child = subprocess.Popen(arguments, start_new_session=True)
        deadline = None
        while True:
            try:
                returncode = child.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                if received_signal is None:
                    continue
                deadline = deadline or time.monotonic() + LIFECYCLE_STOP_TIMEOUT
                if abandoned or time.monotonic() >= deadline:
                    abandoned = True
                    child.kill()
                    child.wait()
                    raise SystemExit(128 + received_signal)
        # The client exited by itself, so no request of ours remains in flight.
        unfinished = False
        if received_signal is not None:
            raise SystemExit(128 + received_signal)
        if returncode:
            raise subprocess.CalledProcessError(returncode, arguments)

    try:
        for signum in handlers:
            signal.signal(signum, interrupt)
        acquiring = True
        # Images are pulled beforehand; never download while holding the reservation.
        reservation = subprocess.run(['docker', 'create', '--name', name, '--network', 'none',
                                      '--label', 'code-server.lifecycle-reservation=true',
                                      '--label', f'code-server.lifecycle-owner={owner}',
                                      '--pull', 'never', '--entrypoint', '/bin/false', image],
                                     capture_output=True, text=True)
        create_returned = True
        if reservation.returncode or not reservation.stdout.strip():
            fail(f'Could not reserve workspace volume {volume["name"]} ({name}): {reservation.stderr.strip()}')
        guard = reservation.stdout.strip()
        changing = True
        yield run
        changing = False
    finally:
        cleaning_up = True
        error = sys.exc_info()[1]
        # Compose can be terminated separately from manage. In either case
        # Docker may still be acting on a request after its client exits.
        child_interrupted = isinstance(error, subprocess.CalledProcessError) and (
            error.returncode < 0 or error.returncode in (129, 130, 143))
        try:
            if changing and (unfinished or child_interrupted):
                print(f'Reservation {name} ({guard}) retained after interruption. '
                      'Verify the Docker operation has finished before removing it or retrying.', file=sys.stderr)
            elif acquiring:
                # Only an interrupted create can leave a container this client never saw.
                if not guard and not create_returned:
                    guard = find_owned_reservation(name, owner)
                if guard:
                    try:
                        subprocess.run(['docker', 'rm', guard], check=True, stdout=subprocess.DEVNULL,
                                       timeout=15, start_new_session=True)
                    except (OSError, subprocess.SubprocessError):
                        message = (f'Could not confirm removal of reservation {name} ({guard}). '
                                   'Inspect it before removing it or retrying.')
                        if error is None and received_signal is None:
                            fail(message)
                        print(message, file=sys.stderr)
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
        if received_signal is not None:
            raise SystemExit(128 + received_signal)


def check_project_name_change(previous, project):
    old_project = previous.get('name')
    if not old_project or project == old_project:
        return
    label = f'label=com.docker.compose.project={old_project}'
    message = f'Cannot change the Compose project name from {old_project} to {project}'
    containers = subprocess.check_output(['docker', 'container', 'ls', '--quiet', '--filter', label], text=True)
    if containers.strip():
        fail(f'{message} while {old_project} has running containers. Keep the saved project name.')
    volumes = set(subprocess.check_output(['docker', 'volume', 'ls', '--quiet', '--filter', label], text=True).split())
    if not volumes and previous.get('volumes'):
        # Volumes reused by Compose may have been created without a project label.
        names = {volume['name'] for volume in previous['volumes'].values()}
        existing = subprocess.check_output(['docker', 'volume', 'ls', '--quiet'], text=True)
        volumes = names.intersection(existing.split())
    if volumes:
        fail(f'{message} while {old_project} has Docker volumes. Keep the saved project name.')


def validate_backup_id(model):
    # Same rule as the backup entrypoint; failing there would leave the sidecar crash-looping.
    backup_id = model.get('services', {}).get('backup', {}).get('environment', {}).get('BACKUP_ID')
    if backup_id is not None and not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', backup_id):
        fail('BACKUP_ID must be 1 to 64 lowercase letters, digits, hyphens or underscores, '
             f'starting with a letter or digit; got: {backup_id!r}')


def configure(args, directory):
    path = configuration_path(directory)
    with env_lock(path):
        configure_locked(args, directory, path)


def configure_locked(args, directory, path, *, upgrade_image=None):
    previous = saved_configuration(directory) if path.exists() else {}
    mode = args.backup if args.backup is not None else previous.get('x-manage-backup', 'none')
    project = args.project_name or previous.get('name')
    check_project_name_change(previous, project)
    # Take the snapshot lock first, then the lock used by file replacement.
    env = directory / '.env'
    with env_lock(env) as lock_fd:
        command = ['docker', 'compose', '--project-directory', str(directory), '--env-file', str(env)]
        if project:
            command += ['--project-name', project]
        files = ['compose.yaml']
        if mode != 'none':
            files += ['compose.backup-common.yaml',
                      'compose.backup-local.yaml' if mode == 'local' else 'compose.backup.yaml']
        for name in files:
            source = directory / name
            if not source.is_file():
                fail(f'Required Compose file is missing: {source}')
            command += ['-f', str(source)]
        if mode == 'local':
            os.environ.update(BACKUP_LOCAL_UID=str(os.getuid()), BACKUP_LOCAL_GID=str(os.getgid()))
        if mode != 'none':
            subprocess.run([sys.executable, str(helper(directory, 'backup-key.py')), str(env),
                            '--lock-fd', str(lock_fd)], check=True, pass_fds=(lock_fd,))
            os.environ.pop('BACKUP_GPG_PASSPHRASE', None)
        host = json.loads(subprocess.check_output(['docker', 'info', '--format', '{{json .}}'], text=True))
        total = int(host['MemTotal'])
        if total <= 0:
            fail('Docker did not report a positive host memory total.')
        os.environ['CODE_SERVER_MEMORY_LIMIT'] = str(total * 4 // 5)
        os.environ['CODE_SERVER_HOSTNAME'] = host['Name']
        render_env = dict(os.environ, CODE_SERVER_IMAGE=upgrade_image) if upgrade_image else None
        model = json.loads(subprocess.check_output(
            command + ['config', '--format', 'json'], text=True, env=render_env))
    validate_backup_id(model)
    if args.pull and not upgrade_image:
        warn_release(model)
    model['x-manage-backup'] = mode
    # Compose config escapes literal dollars for reuse as a Compose file.
    # Keep secrets private, and publish the snapshot only after creation succeeds.
    # The temporary snapshot holds interpolated secrets; main() makes hangups
    # and terminations unwind so this finally block still removes it.
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix='.manage.', suffix='.json', dir=directory)
        with os.fdopen(descriptor, 'w') as output:
            json.dump(model, output)
        configured = compose_command(directory, temporary)
        pull_images(configured, model, refresh=args.pull)
        old_image = None
        old_reference = previous.get('services', {}).get('code-server', {}).get('image', '')
        old_repository = re.split('[:@]', old_reference, maxsplit=1)[0]
        with reserve_workspace(model, configured, use_saved_image=True) as run, reserve_backup(model):
            if upgrade_image and old_repository == upgrade_image.rsplit(':', 1)[0]:
                try:
                    old_image = service_image_id(compose_command(directory, path), 'code-server')
                except (OSError, subprocess.CalledProcessError) as error:
                    print(f'Warning: could not identify the previous workspace image: {error}', file=sys.stderr)
            run(configured + ['create', '--remove-orphans'])
            os.replace(temporary, path)
            if args.start:
                saved = compose_command(directory, path)
                start_workspace(run, saved, model)
                if old_image:
                    remove_superseded_image(old_image, old_reference, saved)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def warn_restore_key(directory, key):
    # Scheduled backups use the key saved by configure, not later .env edits.
    try:
        saved = saved_configuration(directory)['services']['backup']['environment']['GPG_PASSPHRASE']
    except (OSError, ValueError, KeyError, TypeError):
        return
    if saved and saved != key:
        print(f'Warning: BACKUP_GPG_PASSPHRASE in {directory / ".env"} differs from the key the backup service '
              f'uses, saved in {directory / CONFIGURATION} when manage configure last ran. Archives created since '
              'then are encrypted with the saved key. If this restore fails with a wrong key, rerun it with '
              'BACKUP_GPG_PASSPHRASE set to the GPG_PASSPHRASE value from the saved configuration.', file=sys.stderr)


S3_RESTORE_SETTINGS = 'CODE_SERVER_AIO_S3_RESTORE_SETTINGS'
S3_ARCHIVE_SUFFIXES = ('.tar.gz', '.tar.gz.gpg')
S3_SETTINGS = ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_ENDPOINT',
               'AWS_ENDPOINT_PROTO', 'AWS_S3_BUCKET_NAME', 'AWS_S3_BUCKET_LOOKUP',
               'AWS_S3_PATH')


def s3_settings(model):
    if model.get('x-manage-backup') != 's3':
        raise ValueError('S3 backup commands require configured S3 backups.')
    env = model.get('services', {}).get('backup', {}).get('environment', {})
    if not all(env.get(name) for name in
               ('AWS_S3_BUCKET_NAME', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY')):
        raise ValueError('Saved S3 backup settings are incomplete. Run manage configure --backup s3.')
    return {name: env[name] for name in S3_SETTINGS if name in env}


def validate_s3_archive(settings, archive):
    bucket, _, key = archive[len('s3://'):].partition('/')
    if bucket != settings['AWS_S3_BUCKET_NAME'] or not key.endswith(S3_ARCHIVE_SUFFIXES):
        raise ValueError('Use an s3:// URI with the configured bucket and a .tar.gz or .tar.gz.gpg object key.')


@contextmanager
def s3_cli(settings):
    aws = shutil.which('aws')
    if aws is None:
        raise ValueError('Install the AWS CLI to list or restore S3 backups.')
    endpoint = settings.get('AWS_ENDPOINT', 's3.amazonaws.com')
    protocol = settings.get('AWS_ENDPOINT_PROTO', 'https')
    region = 'us-east-1'
    if endpoint.lower().endswith('.r2.cloudflarestorage.com'):
        region = 'auto'
    elif regional := re.fullmatch(r's3[.-]([a-z]{2}(?:-[a-z]+)+-\d+)\.amazonaws\.com(?:\.cn)?', endpoint):
        region = regional[1]

    # Offen uses path-style addressing by default for R2 and custom endpoints.
    lookup = settings.get('AWS_S3_BUCKET_LOOKUP', 'auto')
    virtual_endpoints = ('.amazonaws.com', '.amazonaws.com.cn', '.aliyuncs.com')
    if lookup == 'auto' and endpoint != 'storage.googleapis.com' and not endpoint.endswith(virtual_endpoints):
        lookup = 'path'
    style = {'path': 'path', 'dns': 'virtual', 'virtual': 'virtual', 'auto': 'auto'}.get(lookup)
    if style is None or protocol not in ('https', 'http'):
        raise ValueError('Unsupported S3 bucket lookup style or endpoint protocol in saved configuration.')

    command = [aws, '--region', region]
    # Let the SDK discover the bucket's region when using the standard AWS endpoint.
    if (protocol, endpoint) != ('https', 's3.amazonaws.com'):
        command += ['--endpoint-url', f'{protocol}://{endpoint}']
    env = {name: value for name, value in os.environ.items() if not name.startswith('AWS_')}
    env.pop('BACKUP_GPG_PASSPHRASE', None)
    with tempfile.TemporaryDirectory(prefix='manage-s3-') as directory:
        config = Path(directory) / 'config'
        config.write_text(f'[default]\ns3 =\n    addressing_style = {style}\n', encoding='utf-8')
        env.update(AWS_CONFIG_FILE=str(config), AWS_SHARED_CREDENTIALS_FILE=os.devnull,
                   AWS_ACCESS_KEY_ID=settings['AWS_ACCESS_KEY_ID'],
                   AWS_SECRET_ACCESS_KEY=settings['AWS_SECRET_ACCESS_KEY'],
                   AWS_EC2_METADATA_DISABLED='true', AWS_PAGER='', AWS_CLI_AUTO_PROMPT='off',
                   AWS_RESPONSE_CHECKSUM_VALIDATION='when_required')
        yield command, env


def format_archive_size(byte_count):
    if byte_count < 1024:
        return f'{byte_count} B'
    size = byte_count
    for unit in ('KiB', 'MiB', 'GiB', 'TiB', 'PiB'):
        size /= 1024
        if size < 1024 or unit == 'PiB':
            amount = f'{size:.1f}'.rstrip('0').rstrip('.')
            return f'{amount} {unit}'


def list_s3_archives(settings):
    bucket = settings['AWS_S3_BUCKET_NAME']
    prefix = posixpath.normpath(settings.get('AWS_S3_PATH', ''))
    # Go's path.Join (used by Offen) collapses consecutive leading slashes.
    if prefix.startswith('//'):
        prefix = prefix[1:]
    if prefix == '.':
        prefix = ''
    if prefix and not prefix.endswith('/'):
        prefix += '/'
    with s3_cli(settings) as (command, env):
        result = subprocess.check_output(command + [
            's3api', 'list-objects-v2', '--output', 'json', '--bucket', bucket,
            '--prefix=' + prefix], env=env)
    objects = json.loads(result).get('Contents', [])
    for item in sorted(objects, key=lambda entry: entry['LastModified'], reverse=True):
        key = item['Key']
        if key.endswith(S3_ARCHIVE_SUFFIXES):
            size = format_archive_size(item['Size'])
            print(f"{item['LastModified']}  {size:>12}  s3://{bucket}/{key}")


def manage(args, directory):
    if args.action == 'backup' and args.backup_action == 'restore':
        remote = args.archive.startswith('s3://')
        archive = args.archive if remote else os.path.abspath(args.archive)
        restore = helper(directory, 'restore-home.sh')
        volume = args.volume
        if remote or volume is None:
            model = saved_configuration(directory)
        if remote:
            settings = s3_settings(model)
            validate_s3_archive(settings, archive)
        if volume is None:
            volume = (home_volume(model) or {}).get('name')
            if not isinstance(volume, str) or not volume.strip():
                fail('Cannot find a named Docker volume mounted at /home in the saved configuration. '
                     'Use --volume NAME to select an existing Docker volume.')
        if archive.endswith('.gpg') and not os.environ.get('BACKUP_GPG_PASSPHRASE'):
            if not (directory / '.env').is_file():
                fail('Restore requires BACKUP_GPG_PASSPHRASE or the original .env encryption key.')
            env = dict(os.environ)
            env.pop('BACKUP_GPG_PASSPHRASE', None)
            restore_key = subprocess.check_output([
                'docker', 'compose', '--project-directory', str(directory),
                '--env-file', str(directory / '.env'), '-f', '-', 'config', '--format', 'json'],
                input='services:\n  restore-key:\n    image: unused\n    environment:\n      BACKUP_GPG_PASSPHRASE: ${BACKUP_GPG_PASSPHRASE:?Restore requires the original BACKUP_GPG_PASSPHRASE in .env}\n',
                text=True, env=env)
            os.environ['BACKUP_GPG_PASSPHRASE'] = json.loads(restore_key)['services']['restore-key']['environment'][
                'BACKUP_GPG_PASSPHRASE']
            warn_restore_key(directory, os.environ['BACKUP_GPG_PASSPHRASE'])
        command = ['sh', str(restore), archive, volume]
        if remote:
            os.environ[S3_RESTORE_SETTINGS] = json.dumps(settings)
            os.chdir(directory)
            command.append(str(directory / 'manage.py'))
        os.execvp('sh', command)
    os.chdir(directory)
    if args.action in ('upgrade', 'configure'):
        if not (directory / '.env').is_file():
            fail('Configure .env in the deployment directory before configuring containers.')
        if not os.access(directory / '.env', os.R_OK):
            fail('Cannot read .env. If installed as root, run manage with sudo.')
        if args.action == 'upgrade':
            upgrade(directory, args.version)
        else:
            configure(args, directory)
        return
    if args.action in ('start', 'stop'):
        path = directory / CONFIGURATION
        with env_lock(path):
            model = saved_configuration(directory)
            command = compose_command(directory, path)
            if args.action == 'stop':
                with reserve_backup(model):
                    subprocess.run(command + ['stop'], check=True)
            else:
                if args.pull:
                    pull_images(command, model, refresh=True)
                    # Pulling an older tag succeeds but brings no new fixes; say so.
                    warn_release(model)
                with reserve_workspace(model, command, use_saved_image=args.pull) as run, reserve_backup(model):
                    if args.pull:
                        run(command + ['create'])
                    start_workspace(run, command, model)
        return
    model = saved_configuration(directory)
    command = compose_command(directory, directory / CONFIGURATION)
    if args.action == 'logs':
        command += ['logs']
        if args.follow:
            command += ['--follow']
    elif args.action == 'backup' and args.backup_action == 'run':
        if model.get('x-manage-backup', 'none') == 'none':
            fail('backup --run requires a configured backup service; run manage configure --backup local or s3 --start.')
        # Scheduled and manual runs must share the sidecar's filesystem lock.
        command += ['exec', '-T', 'backup', 'backup']
    elif args.action == 'backup' and args.backup_action == 'list':
        if model.get('x-manage-backup') == 's3':
            list_s3_archives(s3_settings(model))
            return
        if model.get('x-manage-backup') != 'local':
            fail('backup --list requires configured local or S3 backups.')
        command += ['run', '--rm', '--no-deps', '--entrypoint', '/bin/sh', 'backup', '-ec',
                    r'find /archive -maxdepth 1 -type f \( -name "*.tar.gz" -o -name "*.tar.gz.gpg" \) -exec ls -lh {} \;']
    os.execvp(command[0], command)


def warn_release(model):
    try:
        image = model['services']['code-server']['image']
        match = re.fullmatch(r'ghcr\.io/([a-z0-9_.-]+/[a-z0-9_.-]+):(' + TAG[1:] + ')', image)
        if match:
            latest = latest_tag(match[1], timeout=2)
            if latest != 'v' + match[2]:
                # Weekly security rebuilds republish only the latest stable release,
                # so an older pinned tag silently stops receiving fixes.
                print(f'Warning: CODE_SERVER_IMAGE differs from latest release {latest}. Only the latest '
                      'stable release receives weekly security rebuilds; to keep receiving fixes, run '
                      'manage upgrade, then manage configure --pull --start.', file=sys.stderr)
    except Exception:
        pass  # Remain usable offline and with custom images.


# Release bundle members, installed flat beside each other.
DEPLOYMENT_FILES = ('compose.yaml', 'compose.backup-common.yaml', 'compose.backup.yaml',
                    'compose.backup-local.yaml', 'manage', 'manage.py', 'restore-home.sh', 'backup-key.py')


def validate_destination(directory, names):
    if directory.exists() and not directory.is_dir():
        fail(f'Expected a deployment directory: {directory}')
    if os.geteuid() == 0:
        for candidate in (directory, *directory.parents):
            if candidate.exists() or candidate.is_symlink():
                info = candidate.lstat()
                if candidate.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
                    fail(f'Root installation requires root-owned directories without group/world write access: {candidate}')
    for name in names:
        path = directory / name
        if path.is_symlink() or (path.exists() and not path.is_file()):
            fail(f'Expected a regular file: {path}')
        if name != '.env' and path.with_name(name + '.bak').is_dir():
            fail(f'Backup destination must not be a directory: {path}.bak')


def dependencies():
    """Check runtime requirements; the installer never installs host packages."""
    if not shutil.which('docker'):
        fail('A working Docker installation is required before running this installer.')
    if subprocess.run(['docker', 'compose', 'version'], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode:
        fail('Docker Compose is required. Install docker-compose-plugin (Docker CE) or docker-compose '
             "(Debian's docker.io, or Homebrew on macOS), then rerun the installer.")


def require_https(url):
    if urllib.parse.urlsplit(url).scheme != 'https':
        fail('Release downloads require HTTPS for every request and redirect.')


def release_opener():
    class HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            require_https(newurl)
            return super().redirect_request(request, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(HTTPSRedirectHandler())


def download(url):
    require_https(url)
    opener = release_opener()
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'code-server-aio-manage'})
            with opener.open(request, timeout=30) as response:
                return response.read()
        except (OSError, http.client.HTTPException) as error:
            # HTTPError owns a response body even when urlopen raises before
            # entering the response context manager.
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            if isinstance(error, urllib.error.HTTPError) and 400 <= error.code < 500 and error.code != 429:
                raise
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def read_bundle(data):
    """Read the expected regular files from a verified release bundle without extracting it."""
    expected = {*DEPLOYMENT_FILES, '.env.example'}
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as bundle:
        for member in bundle:
            if member.name in expected and member.isreg():
                files[member.name] = bundle.extractfile(member).read()
    missing = expected - files.keys()
    if missing:
        fail('Release bundle lacks required deployment files: ' + ', '.join(sorted(missing)))
    return files


def parse_checksums(checksums):
    # Same rules as install.sh and publish-release.py: 64-hex digests, unique names.
    expected = {}
    for line in checksums.decode().splitlines():
        if not line.strip():
            continue
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or not re.fullmatch(r'[a-f0-9]{64}', fields[0]) or fields[1] in expected:
            fail('Release checksum manifest SHA256SUMS is malformed')
        expected[fields[1]] = fields[0]
    return expected


def merge_settings(original, template, version):
    validate_dotenv(original)
    validate_dotenv(template)
    key = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[=:]')
    existing = {m[1] for line in original.splitlines() if (m := key.match(line))}
    additions, notes = [], []
    for line in template.splitlines(keepends=True):
        if not line.strip() or line.lstrip().startswith('#'):
            notes.append(line)
            continue
        match = key.match(line)
        if match and match[1] not in existing:
            additions.extend(notes + [line])
            existing.add(match[1])
        notes = []
    if additions:
        original += f'\n# Settings added by installer for release {version}\n' + ''.join(additions)
    return original


def select_image(text, image):
    validate_dotenv(text)
    values = list(IMAGE_SETTING.finditer(text))
    if values and re.search(r'@sha256:[a-fA-F0-9]{64}(?=[\s"\x27#]|$)', values[-1][1]):
        print('Warning: keeping existing CODE_SERVER_IMAGE digest pin; verify a new digest or run manage upgrade to deliberately replace it.', file=sys.stderr)
        return text
    return replace_image_setting(text, image)


def replace_image_setting(text, image):
    validate_dotenv(text)
    setting = 'CODE_SERVER_IMAGE=' + image
    if IMAGE_SETTING.search(text):
        return IMAGE_SETTING.sub(lambda _: setting, text)
    return text + ('' if not text or text.endswith('\n') else '\n') + setting + '\n'


def install_files(directory, files, template, version, image, replace_pin=False):
    """Serialize refreshes, stage complete files, and roll back failed replacements."""
    env = directory / '.env'
    with env_lock(env):
        validate_destination(directory, [*files, '.env'])
        text = merge_settings(env.read_text(), template, version) if env.exists() else template
        # Upgrades deliberately replace a digest pin; refreshes keep it.
        text = (replace_image_setting if replace_pin else select_image)(text, image)
        staging = Path(tempfile.mkdtemp(prefix='.install-', dir=directory))
        replaced = []
        originals = {}
        recoverable = True
        try:
            staged_env = staging / '.env'
            staged_env.write_text(text)
            staged_env.chmod(0o600)
            key_helper = staging / 'backup-key.py'
            key_helper.write_bytes(files['backup-key.py'])
            subprocess.run([sys.executable, str(key_helper), str(staged_env)], check=True)
            contents = {**files, '.env': staged_env.read_bytes()}
            for index, (name, data) in enumerate(contents.items()):
                destination = directory / name
                original = staging / f'original-{index}'
                # Capture the existing owner once per file for both copies below.
                info = destination.stat() if destination.exists() else None
                if info is not None:
                    shutil.copy2(destination, original)
                    os.chown(original, info.st_uid, info.st_gid)
                    originals[name] = original
                else:
                    originals[name] = None
                candidate = staging / f'new-{index}'
                candidate.write_bytes(data)
                candidate.chmod(0o600 if name == '.env' else
                                0o755 if name == 'manage' or name.endswith('.sh') else 0o644)
                if name == '.env' and info is not None:
                    os.chown(candidate, info.st_uid, info.st_gid)
            # Keep the previous release files for manual rollback as before.
            for index, name in enumerate(contents):
                if name != '.env' and originals[name] is not None:
                    backup = staging / f'backup-{index}'
                    shutil.copy2(originals[name], backup)
                    os.replace(backup, directory / (name + '.bak'))
            try:
                for index, name in enumerate(contents):
                    os.replace(staging / f'new-{index}', directory / name)
                    replaced.append(name)
            except BaseException:
                for name in reversed(replaced):
                    try:
                        if originals[name] is None:
                            (directory / name).unlink()
                        else:
                            os.replace(originals[name], directory / name)
                    except OSError:
                        recoverable = False
                if not recoverable:
                    print(f'Installation rollback incomplete; recovery files retained in {staging}', file=sys.stderr)
                raise
        finally:
            if recoverable:
                shutil.rmtree(staging)
    for name in files:
        print(f'Updated {directory / name}')


def install(args, upgrade=False):
    default = '/opt/code-server' if os.geteuid() == 0 else Path.home() / 'code-server'
    directory = Path(os.path.abspath(args.directory or default))
    repository = args.repository or 'Weeks-Consulting/code-server-aio'
    version = args.version
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repository):
        fail('Repository must be owner/repo.')
    if version and not re.fullmatch(TAG, version):
        fail(f'Expected a release tag such as v4.141.0 or v4.141.0-1; got: {version}')
    validate_destination(directory, [*DEPLOYMENT_FILES, '.env'])
    dependencies()
    image = 'ghcr.io/' + repository.lower() + ':' + (version[1:] if version else 'latest')
    if args.dry_run:
        print(f'Would use release {version} from {repository}.' if version else
              f'Would resolve the latest stable release from https://github.com/{repository}/releases/latest.')
        if not directory.is_dir():
            print(f'Would create deployment directory: {directory}')
        for name in DEPLOYMENT_FILES:
            if (directory / name).is_file():
                print(f'Would save {directory / name} as {name}.bak before replacing it.')
            print(f'Would install {directory / name} from the release bundle (all files are required).')
        print(f'Would preserve existing {directory}/.env values and append missing release settings.' if (directory / '.env').exists() else
              f'Would create {directory}/.env from the release template with its image selected.')
        print(f'Would set CODE_SERVER_IMAGE={image} in .env unless an existing digest pin is present.')
        print('Would set .env mode to 600 and generate a backup key only if missing or empty.')
        print('Dry run complete. No releases downloaded, files changed, or services started.')
        return
    if not version:
        version, repository = published_release(repository)
    base = f'https://github.com/{repository}/releases/download/{version}'
    try:
        bundle = download(f'{base}/{BUNDLE}')
        checksums = download(f'{base}/SHA256SUMS')
    except urllib.error.HTTPError as error:
        if error.code == 404:
            fail(f'Release {version} lacks required deployment files or SHA256SUMS; '
                 'if it was just published, retry after publishing finishes.')
        raise
    if parse_checksums(checksums).get(BUNDLE) != hashlib.sha256(bundle).hexdigest():
        fail('Release checksum missing or incorrect: ' + BUNDLE)
    files = read_bundle(bundle)
    template = select_image(files.pop('.env.example').decode(), image)
    # Finish downloads and validation before changing any deployment files.
    directory.mkdir(parents=True, exist_ok=True)
    validate_destination(directory, list(files) + ['.env'])
    env = directory / '.env'
    if upgrade:
        snapshot = configuration_path(directory)
        with env_lock(snapshot):
            install_files(directory, files, template, version, image, replace_pin=True)
            print(f'\nInstalled deployment files in {directory} for release {version}.')
            print('Set CODE_SERVER_IMAGE=' + image)
            configure_args = argparse.Namespace(backup=None, project_name=None, pull=True, start=True)
            try:
                configure_locked(configure_args, directory, snapshot, upgrade_image=image)
            except Exception as error:
                fail(f'Release files were installed, but the deployment could not be applied: {error}. '
                     'Retry manage upgrade or run manage configure --pull --start.')
        return
    install_files(directory, files, template, version, image)
    print(f'\nInstalled deployment files in {directory} (release {version}).')
    print(f'Edit {env} to configure Cloudflare Access and your tunnel before starting.')
    print(f'Then run sh "{directory}/manage" configure --pull --start to launch. No services have been started.')


def validate_args(args):
    if args.follow and args.action != 'logs':
        fail('-f and --follow are only supported by the logs command')
    for option in ('directory', 'repository', 'version', 'project_name', 'archive', 'volume'):
        if getattr(args, option) == '':
            fail('Missing value for --' + option.replace('_', '-'))
    if args.action != 'configure' and (args.backup is not None or args.project_name is not None or args.start):
        fail('--backup, --project-name, and --start are only supported by configure')
    if args.pull and args.action not in ('configure', 'start'):
        fail('--pull is only supported by configure and start')
    if args.action != 'install' and (args.directory is not None or args.repository is not None or args.dry_run):
        fail('--directory, --repository, and --dry-run are only supported by install')
    if args.version is not None and args.action not in ('install', 'upgrade'):
        fail('--version is only supported by install and upgrade')
    if args.action == 'backup' and args.backup_action is None:
        fail('backup requires one of --run, --list, or --restore')
    if args.action != 'backup' and args.backup_action is not None:
        fail('--run, --list, and --restore are only supported by backup')
    if args.action == 'backup' and args.backup_action == 'restore':
        if not args.archive:
            fail('backup --restore requires --archive FILE_OR_URI')
    elif args.archive or args.volume:
        fail('--archive and --volume are only supported by backup --restore')


def unwind_on_termination():
    """Make SIGHUP and SIGTERM unwind like Ctrl-C, so every finally block runs.

    Temporary snapshots and install staging hold copies of secrets; any catchable
    interruption must remove them. Signals the caller deliberately ignores (nohup
    ignores SIGHUP) stay ignored. reserve_workspace installs its own handlers for
    lifecycle Compose commands and restores these afterwards.
    """
    def terminate(signum, frame):
        raise SystemExit(128 + signum)

    for signum in (signal.SIGHUP, signal.SIGTERM):
        if signal.getsignal(signum) == signal.SIG_DFL:
            signal.signal(signum, terminate)


def main():
    unwind_on_termination()
    args = parser().parse_args()
    validate_args(args)
    if args.action == 'install':
        install(args)
    else:
        manage(args, Path(__file__).resolve().parent)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
    except KeyboardInterrupt:
        # For example, Ctrl-C during a pull, before any lifecycle lock is held.
        sys.exit(130)
    except (OSError, ValueError, KeyError, http.client.HTTPException, tarfile.TarError) as error:
        sys.exit(str(error))
