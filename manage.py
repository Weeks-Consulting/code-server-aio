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
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request


TAG = r'v\d+\.\d+\.\d+(?:-(?:0|[1-9]\d*))?'
DEFAULT_IMAGE = "ghcr.io/weeks-consulting/code-server-aio:latest"
BUNDLE = "code-server-aio.tar.gz"
IMAGE_SETTING = re.compile(r"^[ \t]*(?:export[ \t]+)?CODE_SERVER_IMAGE[ \t]*=(.*)$", re.M)


def fail(message):
    raise ValueError(message)


def helper(directory, name):
    for path in (directory / name, directory / "build/tools" / name):
        if path.is_file():
            return path
    fail(f"{name} helper is missing; reinstall this release.")


def validate_dotenv(text):
    """Require quoted dotenv values to end on the same physical line."""
    assignment = re.compile(r"^[ \t]*(?:export[ \t]+)?[A-Za-z_][A-Za-z0-9_]*[ \t]*=[ \t]*(.*)$")
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
    with urllib.request.urlopen(request, timeout=timeout) as response:
        release = json.load(response)
    return stable_tag(release)


def published_release(repository):
    """Return the latest stable tag and the repository's canonical owner/repo spelling."""
    release = json.loads(download(f'https://api.github.com/repos/{repository}/releases/latest'))
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
        yield


def upgrade(directory):
    """Install the latest release's files and image together so they stay on one release."""
    path = directory / ".env"
    try:
        # A checkout's deployment files are tracked source; never overwrite them.
        if (directory / "build/tools/install.sh").is_file():
            raise ValueError("upgrade refreshes installed deployments. In a source checkout, check out "
                             "the release tag with git and set CODE_SERVER_IMAGE in .env.")
        if path.is_symlink() or not path.is_file():
            raise ValueError("Expected a regular .env file")
        version, repository = published_release(image_repository(path.read_text()))
        # install_files rereads .env under the lock before changing it.
        install(argparse.Namespace(directory=str(directory), repository=repository,
                                   version=version, dry_run=False), upgrade=True)
    except Exception as error:
        sys.exit("Unable to upgrade: " + str(error))


def parser():
    result = argparse.ArgumentParser(
        prog='manage', usage='%(prog)s [COMMAND] [OPTIONS]',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Install and manage a code-server deployment.\n'
                    'Commands and options may appear in any order. With no command, start is used.\n\n'
                    'commands (brackets mark optional arguments):\n'
                    '  configure [--backup {none,s3,local}] [--project-name NAME] [--pull] [--start]\n'
                    '      Create/update containers and save configuration; --start starts them afterward.\n'
                    '  start\n'
                    '      Start existing configured containers (default command).\n'
                    '  stop\n'
                    '      Stop containers; keep containers, networks, and volumes.\n'
                    '  logs [-f|--follow]\n'
                    '      Print logs from the saved deployment; add -f to follow.\n'
                    '  upgrade\n'
                    '      Install the latest release and image setting; no container changes.\n'
                    '  backup\n'
                    '      Run a backup inside the configured, running backup service.\n'
                    '  backups\n'
                    '      List archives from the configured local backup destination.\n'
                    '  restore --archive FILE --volume NAME\n'
                    '      Replace an existing home volume.\n'
                    '  install [--directory PATH] [--repository OWNER/REPO] [--version TAG] [--dry-run]\n'
                    '      Install or refresh deployment files; no services started.\n'
                    '  All commands accept -h or --help.',
        epilog='examples:\n'
               '  manage configure --pull --start                Configure and start\n'
               '  manage configure --backup local --pull --start  Enable local backups and start\n'
               '  manage stop                                    Stop existing containers\n'
               '  manage start                                   Resume existing containers\n'
               '  manage logs -f                                 Follow service logs\n'
               '  manage backup                                  Run a backup now\n'
               '  manage restore --archive FILE --volume NAME\n\n'
               'configure reads .env and Compose files beside manage and saves a private configuration.\n'
               'Later commands use that saved configuration, including backup mode and project name.\n'
               'Run configure again to apply changes. If installed with sudo, run with sudo.')
    result.add_argument('action', nargs='?', default='start',
                        choices=['install', 'configure', 'start', 'stop', 'logs', 'upgrade', 'backup', 'backups', 'restore'],
                        help=argparse.SUPPRESS)
    configure = result.add_argument_group('configure options')
    configure.add_argument('--backup', choices=['none', 's3', 'local'],
                           help='Backup services to include (default: saved mode, or none on first configure)')
    configure.add_argument('--project-name', metavar='NAME', help='Compose project name (default: saved name, or Compose default)')
    configure.add_argument('--pull', action='store_true', help='Pull configured images before creating containers')
    configure.add_argument('--start', action='store_true', help='Start containers after configuring them')
    logs = result.add_argument_group('logs options')
    logs.add_argument('-f', '--follow', action='store_true', help='Keep streaming new log output')
    restore = result.add_argument_group('restore options (both required)')
    restore.add_argument('--archive', metavar='FILE', help='Backup archive to restore (.tar.gz or .tar.gz.gpg)')
    restore.add_argument('--volume', metavar='NAME', help='Existing home volume to replace')
    install = result.add_argument_group('install options')
    install.add_argument('--directory', metavar='PATH', help='Installation directory (default: ~/code-server, or /opt/code-server as root)')
    install.add_argument('--repository', metavar='OWNER/REPO', help='GitHub repository (default: Weeks-Consulting/code-server-aio)')
    install.add_argument('--version', metavar='TAG', help='Release tag vX.Y.Z[-N] (default: latest stable release, image tag: latest)')
    install.add_argument('--dry-run', action='store_true', help='Preview installation without changes or network requests')
    return result


CONFIGURATION = '.manage.compose.json'


def saved_configuration(directory):
    path = directory / CONFIGURATION
    if path.is_symlink():
        fail('Expected a regular saved configuration file: ' + str(path))
    if not path.is_file():
        fail('No saved configuration. Run manage configure --start first; add --backup local or s3 if needed.')
    return json.loads(path.read_text())


def compose_command(directory, path):
    # The snapshot is already interpolated; do not read changing deployment .env values.
    return ['docker', 'compose', '--project-directory', str(directory),
            '--env-file', '/dev/null', '-f', str(path)]


def configure(args, directory):
    path = directory / CONFIGURATION
    with env_lock(path):
        previous = saved_configuration(directory) if path.exists() else {}
        mode = args.backup if args.backup is not None else previous.get('x-manage-backup', 'none')
        project = args.project_name or previous.get('name')
        command = ['docker', 'compose', '--project-directory', str(directory),
                   '--env-file', str(directory / '.env')]
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
            subprocess.run([sys.executable, str(helper(directory, 'backup-key.py')), str(directory / '.env')], check=True)
            os.environ.pop('BACKUP_GPG_PASSPHRASE', None)
        total = int(subprocess.check_output(['docker', 'info', '--format', '{{.MemTotal}}'], text=True).strip())
        if total <= 0:
            fail('Docker did not report a positive host memory total.')
        os.environ['CODE_SERVER_MEMORY_LIMIT'] = str(total * 4 // 5)
        if args.pull:
            warn_release(command)
        model = json.loads(subprocess.check_output(command + ['config', '--format', 'json'], text=True))
        model['x-manage-backup'] = mode
        # Compose config escapes literal dollars for reuse as a Compose file.
        # Keep secrets private, and publish the snapshot only after creation succeeds.
        descriptor, temporary = tempfile.mkstemp(prefix='.manage.', suffix='.json', dir=directory)
        try:
            with os.fdopen(descriptor, 'w') as output:
                json.dump(model, output)
            create = compose_command(directory, temporary) + ['create', '--remove-orphans']
            if args.pull:
                create += ['--pull', 'always']
            subprocess.run(create, check=True)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        if args.start:
            subprocess.run(compose_command(directory, path) + ['start'], check=True)


def manage(args, directory):
    if args.action == 'restore':
        archive = os.path.abspath(args.archive)
        restore = helper(directory, 'restore-home.sh')
        if archive.endswith('.gpg') and not os.environ.get('BACKUP_GPG_PASSPHRASE'):
            if not (directory / '.env').is_file():
                fail('Restore requires BACKUP_GPG_PASSPHRASE or the original .env encryption key.')
            env = dict(os.environ)
            env.pop('BACKUP_GPG_PASSPHRASE', None)
            settings = subprocess.check_output([
                'docker', 'compose', '--project-directory', str(directory),
                '--env-file', str(directory / '.env'), '-f', '-', 'config', '--format', 'json'],
                input='services:\n  restore-key:\n    image: unused\n    environment:\n      BACKUP_GPG_PASSPHRASE: ${BACKUP_GPG_PASSPHRASE:?Restore requires the original BACKUP_GPG_PASSPHRASE in .env}\n',
                text=True, env=env)
            # Compose serializes each literal '$' as '$$'; decode to the value backups used.
            os.environ['BACKUP_GPG_PASSPHRASE'] = json.loads(settings)['services']['restore-key']['environment'][
                'BACKUP_GPG_PASSPHRASE'].replace('$$', '$')
        os.execvp('sh', ['sh', str(restore), archive, args.volume])
    os.chdir(directory)
    if args.action in ('upgrade', 'configure'):
        if not (directory / '.env').is_file():
            fail('Configure .env in the deployment directory before configuring containers.')
        if not os.access(directory / '.env', os.R_OK):
            fail('Cannot read .env. If installed as root, run manage with sudo.')
        if args.action == 'upgrade':
            upgrade(directory)
        else:
            configure(args, directory)
        return
    model = saved_configuration(directory)
    command = compose_command(directory, directory / CONFIGURATION)
    if args.action in ('start', 'stop'):
        command += [args.action]
    elif args.action == 'logs':
        command += ['logs']
        if args.follow:
            command += ['--follow']
    elif args.action == 'backup':
        if model.get('x-manage-backup', 'none') == 'none':
            fail('backup requires a configured backup service; run manage configure --backup local or s3 --start.')
        # Scheduled and manual runs must share the sidecar's filesystem lock.
        command += ['exec', '-T', 'backup', 'backup']
    elif args.action == 'backups':
        if model.get('x-manage-backup') != 'local':
            fail('backups requires configured local backups; use your storage provider to list S3 archives.')
        command += ['run', '--rm', '--no-deps', '--entrypoint', '/bin/sh', 'backup', '-ec',
                    'find /archive -maxdepth 1 -type f \( -name "*.tar.gz" -o -name "*.tar.gz.gpg" \) -exec ls -lh {} \;']
    os.execvp(command[0], command)


def warn_release(command):
    try:
        model = subprocess.check_output(command + ['config', '--format', 'json'], stderr=subprocess.DEVNULL, text=True)
        image = json.loads(model)['services']['code-server']['image']
        match = re.fullmatch(r'ghcr.io/([a-z0-9_.-]+/[a-z0-9_.-]+):(\d+\.\d+\.\d+(?:-(?:0|[1-9]\d*))?)', image)
        if match:
            latest = latest_tag(match[1], timeout=2)
            if latest != 'v' + match[2]:
                print('Warning: CODE_SERVER_IMAGE differs from latest release ' + latest + '. Review .env before pulling; pinned older tags may miss security fixes.', file=sys.stderr)
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


def download(url):
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'code-server-aio-manage'})
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except (OSError, http.client.HTTPException) as error:
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
    expected = {}
    for line in checksums.decode().splitlines():
        if line.strip():
            digest, name = line.split(maxsplit=1)
            expected[name] = digest
    return expected


def merge_settings(original, template, version):
    validate_dotenv(original)
    validate_dotenv(template)
    key = re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=')
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
                if destination.exists():
                    shutil.copy2(destination, original)
                    info = destination.stat()
                    os.chown(original, info.st_uid, info.st_gid)
                    originals[name] = original
                else:
                    originals[name] = None
                candidate = staging / f'new-{index}'
                candidate.write_bytes(data)
                candidate.chmod(0o600 if name == '.env' else
                                0o755 if name == 'manage' or name.endswith('.sh') else 0o644)
                if name == '.env' and destination.exists():
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
    install_files(directory, files, template, version, image, replace_pin=upgrade)
    if upgrade:
        print(f'\nUpgraded deployment files in {directory} to release {version}.')
        print('Set CODE_SERVER_IMAGE=' + image)
        print(f'Review new settings in {env}, then run manage configure --pull --start to apply the upgrade.')
        return
    print(f'\nInstalled deployment files in {directory} (release {version}).')
    print(f'Edit {env} to configure Cloudflare Access and your tunnel before starting.')
    print(f'Then run sh "{directory}/manage" configure --pull --start to launch. No services have been started.')


def validate_args(args):
    if args.follow and args.action != 'logs':
        fail('-f and --follow are only supported by the logs command')
    for option in ('directory', 'repository', 'version', 'project_name', 'archive', 'volume'):
        if getattr(args, option) == '':
            fail('Missing value for --' + option.replace('_', '-'))
    if args.action != 'configure' and (args.backup is not None or args.project_name is not None or args.pull or args.start):
        fail('--backup, --project-name, --pull, and --start are only supported by configure')
    if args.action != 'install' and (args.directory is not None or args.repository is not None or args.version is not None or args.dry_run):
        fail('--directory, --repository, --version, and --dry-run are only supported by install')
    if args.action == 'restore':
        if not args.archive or not args.volume:
            fail('restore requires --archive FILE and --volume NAME')
    elif args.archive or args.volume:
        fail('--archive and --volume are only supported by restore')


def main():
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
    except (OSError, ValueError, KeyError, http.client.HTTPException, tarfile.TarError) as error:
        sys.exit(str(error))
