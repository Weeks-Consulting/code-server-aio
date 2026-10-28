#!/usr/bin/env python3
"""Install and manage a published Compose deployment using Python's standard library."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


def fail(message):
    raise ValueError(message)


def helper(directory, name):
    for path in (directory / name, directory / "build/tools" / name):
        if path.is_file():
            return path
    fail(f"{name} helper is missing; reinstall this release.")


def read_image_setting(original):
    pattern = re.compile(r"^[ \t]*(?:export[ \t]+)?CODE_SERVER_IMAGE[ \t]*=(.*)$", re.M)
    matches = list(pattern.finditer(original))
    image = "ghcr.io/weeks-consulting/code-server-aio:latest"
    if matches:
        value = matches[-1].group(1).strip()
        if value.startswith(("\"", "'")):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (value[end + 1:].strip() and not value[end + 1:].strip().startswith("#")):
                raise ValueError("Invalid CODE_SERVER_IMAGE quoting")
            image = value[1:end]
        else:
            image = re.split(r"\s+#", value, 1)[0].strip()
    image = image or "ghcr.io/weeks-consulting/code-server-aio:latest"
    match = re.fullmatch(r"ghcr\.io/([a-z0-9_.-]+/[a-z0-9_.-]+)(?::[A-Za-z0-9_.-]+)?(?:@sha256:[a-f0-9]{64})?", image)
    if not match:
        raise ValueError("CODE_SERVER_IMAGE must name a ghcr.io/owner/repo image")
    repository = match[1]
    return pattern, matches, repository


def latest_tag(repository):
    request = urllib.request.Request(
        "https://api.github.com/repos/" + repository + "/releases/latest",
        headers={"User-Agent": "code-server-aio-manage", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        release = json.load(response)
    tag = release["tag_name"]
    if release.get("draft") or release.get("prerelease") or not re.fullmatch(r"v\d+\.\d+\.\d+(?:-[1-9]\d*)?", tag):
        raise ValueError("GitHub returned an invalid stable release tag")
    return tag


def write_env_atomic(path, updated, owner):
    # Finish the lookup and validation before replacing the configuration.
    descriptor, temporary = tempfile.mkstemp(prefix=".env-upgrade-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            current = os.fstat(output.fileno())
            if (current.st_uid, current.st_gid) != (owner.st_uid, owner.st_gid):
                os.fchown(output.fileno(), owner.st_uid, owner.st_gid)
            os.fchmod(output.fileno(), 0o600)
            output.write(updated)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def upgrade(directory):
    path = directory / ".env"
    try:
        if path.is_symlink():
            raise ValueError("Expected a regular .env file, not a symlink")
        lock = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        owner = path.stat()
        lock_owner = os.fstat(lock)
        if (lock_owner.st_uid, lock_owner.st_gid) != (owner.st_uid, owner.st_gid):
            os.fchown(lock, owner.st_uid, owner.st_gid)
        guard = os.fdopen(lock, "w")
        fcntl.flock(guard, fcntl.LOCK_EX)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Expected a regular .env file")
        owner = path.stat()
        original = path.read_text()
        pattern, matches, repository = read_image_setting(original)
        selected = "ghcr.io/" + repository + ":" + latest_tag(repository)[1:]
        setting = "CODE_SERVER_IMAGE=" + selected
        updated = pattern.sub(lambda _: setting, original)
        if not matches:
            updated += ("" if not updated or updated.endswith("\n") else "\n") + setting + "\n"
        write_env_atomic(path, updated, owner)
        print("Set CODE_SERVER_IMAGE=" + selected)
        print("Run manage --pull with your usual launch options to apply the upgrade.")
    except Exception as error:
        sys.exit("Unable to upgrade image: " + str(error))


def parser():
    result = argparse.ArgumentParser(
        prog='manage', description=__doc__,
        epilog='start (default): start services; stop: remove containers and keep volumes; '
               'upgrade: update the image setting without restarting; backup: run a backup; '
               'backups: list local archives; restore: replace an explicit home volume; '
               'install: install or refresh deployment files. Commands and options may appear in any order.')
    result.add_argument('action', nargs='?', default='start',
                        choices=['install', 'start', 'stop', 'upgrade', 'backup', 'backups', 'restore'])
    result.add_argument('--backup', choices=['none', 's3', 'local'], default='none')
    result.add_argument('--foreground', action='store_true')
    result.add_argument('--pull', action='store_true')
    result.add_argument('--project-name')
    result.add_argument('--archive')
    result.add_argument('--volume')
    result.add_argument('--directory', help='Installation directory (default: /opt/code-server)')
    result.add_argument('--repository', help='GitHub owner/repo (default: Weeks-Consulting/code-server-aio)')
    result.add_argument('--version', help='Release tag vX.Y.Z[-N] (default image: latest)')
    result.add_argument('--dry-run', action='store_true', help='Preview installation without changes or network requests')
    return result


def manage(args, directory):
    if args.action != 'start' and (args.foreground or args.pull):
        fail('--foreground and --pull are only supported by the start command')
    if args.action == 'restore':
        if not args.archive or not args.volume:
            fail('restore requires --archive FILE and --volume NAME')
        if args.backup != 'none' or args.project_name:
            fail('restore targets an explicit volume; omit --backup and --project-name')
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
            os.environ['BACKUP_GPG_PASSPHRASE'] = json.loads(settings)['services']['restore-key']['environment']['BACKUP_GPG_PASSPHRASE']
        os.execvp('sh', ['sh', str(restore), archive, args.volume])
    if args.archive or args.volume:
        fail('--archive and --volume are only supported by restore')
    if args.action == 'backup' and args.backup == 'none':
        fail('backup requires --backup s3 or local')
    if args.action == 'backups' and args.backup != 'local':
        fail('backups requires --backup local; use your storage provider to list S3 archives')
    os.chdir(directory)
    if not (directory / '.env').is_file():
        fail('Configure .env in the deployment directory before starting.')
    if not os.access(directory / '.env', os.R_OK):
        fail('Cannot read .env. If installed as root, run manage with sudo.')
    if args.action == 'upgrade':
        if args.backup != 'none' or args.project_name:
            fail('upgrade only updates .env; omit --backup and --project-name')
        upgrade(directory)
        return
    command = ['docker', 'compose', '--project-directory', str(directory), '--env-file', str(directory / '.env')]
    if args.project_name:
        command += ['--project-name', args.project_name]
    files = ['compose.yaml']
    if args.backup != 'none':
        files += ['compose.backup-common.yaml',
                  'compose.backup-local.yaml' if args.backup == 'local' else 'compose.backup.yaml']
    if args.backup == 'local':
        os.environ.update(BACKUP_LOCAL_UID=str(os.getuid()), BACKUP_LOCAL_GID=str(os.getgid()))
    for name in files:
        path = directory / name
        if not path.is_file():
            fail(f'Required Compose file is missing: {path}')
        command += ['-f', str(path)]
    if args.backup != 'none' and args.action in ('start', 'backup'):
        subprocess.run([sys.executable, str(helper(directory, 'backup-key.py')), str(directory / '.env')], check=True)
        os.environ.pop('BACKUP_GPG_PASSPHRASE', None)
    if args.action == 'start':
        if args.pull:
            warn_release(command)
        command += ['up', '--remove-orphans']
        if not args.foreground:
            command += ['-d']
        if args.pull:
            command += ['--pull', 'always']
    elif args.action == 'stop':
        command += ['down', '--remove-orphans']
    elif args.action == 'backup':
        command += ['run', '--rm', '--no-deps', '--entrypoint', 'backup', 'backup']
    elif args.action == 'backups':
        command += ['run', '--rm', '--no-deps', '--entrypoint', '/bin/sh', 'backup', '-ec',
                    'find /archive -maxdepth 1 -type f \\( -name "*.tar.gz" -o -name "*.tar.gz.gpg" \\) -exec ls -lh {} \\;']
    os.execvp(command[0], command)


def warn_release(command):
    try:
        model = subprocess.check_output(command + ['config', '--format', 'json'], stderr=subprocess.DEVNULL, text=True)
        image = json.loads(model)['services']['code-server']['image']
        match = re.fullmatch(r'ghcr.io/([a-z0-9_.-]+/[a-z0-9_.-]+):(\d+\.\d+\.\d+(?:-[1-9]\d*)?)', image)
        if match:
            request = urllib.request.Request('https://api.github.com/repos/' + match[1] + '/releases/latest',
                                             headers={'User-Agent': 'code-server-aio-manage'})
            with urllib.request.urlopen(request, timeout=2) as response:
                latest = json.load(response)['tag_name']
            if re.fullmatch(r'v\d+\.\d+\.\d+(?:-[1-9]\d*)?', latest) and latest != 'v' + match[2]:
                print('Warning: CODE_SERVER_IMAGE differs from latest release ' + latest + '. Review .env before pulling; pinned older tags may miss security fixes.', file=sys.stderr)
    except Exception:
        pass  # Remain usable offline and with custom images.


DEPLOYMENT_FILES = {name: name for name in (
    'compose.yaml', 'compose.backup-common.yaml', 'compose.backup.yaml',
    'compose.backup-local.yaml', 'manage', 'manage.py')}
DEPLOYMENT_FILES.update({
    'restore-home.sh': 'build/tools/restore-home.sh',
    'backup-key.py': 'build/tools/backup-key.py',
})
TAG = r'v\d+\.\d+\.\d+(?:-[1-9]\d*)?'


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


def dependencies(dry_run):
    if not shutil.which('docker'):
        fail('A working Docker installation is required before running this installer.')
    def available(command):
        return subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    def run(command):
        if dry_run:
            print('Would run: ' + ' '.join(command))
        else:
            subprocess.run(command, check=True)
    compose_missing = not available(['docker', 'compose', 'version'])
    packages = [] if shutil.which('gpg') else ['gnupg']
    system = platform.system()
    if system == 'Darwin':
        if compose_missing:
            packages.append('docker-compose')
        if packages:
            if not shutil.which('brew'):
                fail('Install Homebrew to install the missing runtime dependencies.')
            if os.geteuid() == 0:
                fail('Run the installer without sudo on macOS so Homebrew can install dependencies; choose a directory in your home folder.')
            run(['brew', 'install', *packages])
        if compose_missing:
            plugins = Path(os.environ.get('DOCKER_CONFIG', str(Path.home() / '.docker'))) / 'cli-plugins'
            if dry_run:
                print(f'Would register the Homebrew Compose plugin in {plugins} if Docker cannot discover it.')
            elif not available(['docker', 'compose', 'version']):
                target = plugins / 'docker-compose'
                if target.exists() or target.is_symlink():
                    fail(f'Docker cannot load existing plugin {target}; repair it and rerun the installer.')
                prefix = subprocess.check_output(['brew', '--prefix'], text=True).strip()
                plugins.mkdir(parents=True, exist_ok=True)
                target.symlink_to(Path(prefix) / 'lib/docker/cli-plugins/docker-compose')
    elif system == 'Linux' and Path('/etc/debian_version').is_file():
        def installed(package):
            result = subprocess.run(['dpkg-query', '-W', '-f=${Status}', package], capture_output=True, text=True)
            return result.returncode == 0 and result.stdout.strip() == 'install ok installed'
        if not installed('ca-certificates'):
            packages.append('ca-certificates')
        if compose_missing:
            packages.append('docker-compose-plugin' if installed('docker-ce-cli') else 'docker-compose')
        if packages:
            prefix = [] if os.geteuid() == 0 else ['sudo']
            if prefix and not shutil.which('sudo'):
                fail('Install missing runtime dependencies as root or install sudo.')
            run(prefix + ['apt-get', 'update'])
            run(prefix + ['apt-get', 'install', '-y', *packages])
    else:
        fail('The installer supports Debian Linux and macOS.')
    if not dry_run:
        if not shutil.which('gpg'):
            fail('Install gpg before continuing.')
        subprocess.run(['docker', 'compose', 'version'], check=True)


def download(url):
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'code-server-aio-manage'})
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except OSError:
            if attempt == 3:
                raise


def merge_settings(original, template, version):
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
    pattern = re.compile(r'^[ \t]*(?:export[ \t]+)?CODE_SERVER_IMAGE[ \t]*=(.*)$', re.M)
    values = list(pattern.finditer(text))
    if values and re.search(r'@sha256:[a-fA-F0-9]{64}(?=[\s"\x27#]|$)', values[-1][1]):
        print('Warning: keeping existing CODE_SERVER_IMAGE digest pin; verify a new digest or run manage upgrade to deliberately replace it.', file=sys.stderr)
        return text
    setting = 'CODE_SERVER_IMAGE=' + image
    if values:
        return pattern.sub(lambda _: setting, text)
    return text + ('' if not text or text.endswith('\n') else '\n') + setting + '\n'


def install(args):
    directory = Path(os.path.abspath(args.directory or '/opt/code-server'))
    repository = args.repository or 'Weeks-Consulting/code-server-aio'
    version = args.version
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', repository):
        fail('Repository must be owner/repo.')
    if version and not re.fullmatch(TAG, version):
        fail(f'Expected a release tag such as v4.141.0 or v4.141.0-1; got: {version}')
    validate_destination(directory, [*DEPLOYMENT_FILES, '.env'])
    dependencies(args.dry_run)
    image = 'ghcr.io/' + repository.lower() + ':' + (version[1:] if version else 'latest')
    if args.dry_run:
        print(f'Would use release {version} from {repository}.' if version else
              f'Would resolve the latest stable release from https://github.com/{repository}/releases/latest.')
        if not directory.is_dir():
            print(f'Would create deployment directory: {directory}')
        for name in DEPLOYMENT_FILES:
            if (directory / name).is_file():
                print(f'Would save {directory / name} as {name}.bak before replacing it.')
            print(f'Would download and install {directory / name} (all files are required).')
        print(f'Would preserve existing {directory}/.env values and append missing release settings.' if (directory / '.env').exists() else
              f'Would create {directory}/.env from the release template with its image selected.')
        print(f'Would set CODE_SERVER_IMAGE={image} in .env unless an existing digest pin is present.')
        print('Would set .env mode to 600 and generate a backup key only if missing or empty.')
        print('Dry run complete. No packages installed, releases downloaded, files changed, or services started.')
        return
    if not version:
        release = json.loads(download(f'https://api.github.com/repos/{repository}/releases/latest'))
        version = release['tag_name']
        if release.get('draft') or release.get('prerelease') or not re.fullmatch(TAG, version):
            fail('GitHub returned an invalid stable release tag')
    base = f'https://raw.githubusercontent.com/{repository}/refs/tags/{version}'
    sources = {**DEPLOYMENT_FILES, '.env.example': '.env.example'}
    try:
        files = {name: download(f'{base}/{source}') for name, source in sources.items()}
        checksums = download(f'https://github.com/{repository}/releases/download/{version}/SHA256SUMS')
    except urllib.error.HTTPError as error:
        if error.code == 404:
            fail(f'Release {version} lacks required deployment files or SHA256SUMS; '
                 "use that release's own installer.")
        raise
    expected = {}
    for line in checksums.decode().splitlines():
        digest, name = line.split(maxsplit=1)
        expected[name] = digest
    for name, data in files.items():
        if expected.get(sources[name]) != hashlib.sha256(data).hexdigest():
            fail('Release checksum missing or incorrect: ' + name)
    template = select_image(files.pop('.env.example').decode(), image)
    # Finish downloads and validation before changing any deployment files.
    directory.mkdir(parents=True, exist_ok=True)
    validate_destination(directory, list(files) + ['.env'])
    for name, data in files.items():
        destination = directory / name
        if destination.exists():
            destination.replace(directory / (name + '.bak'))
        destination.write_bytes(data)
        destination.chmod(0o755 if name == 'manage' or name.endswith('.sh') else 0o644)
        print(f'Updated {destination}')
    env = directory / '.env'
    # Share the same lock as startup key generation and image upgrades.
    lock = os.open(str(env) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    if env.exists():
        owner = env.stat()
        current = os.fstat(lock)
        if (owner.st_uid, owner.st_gid) != (current.st_uid, current.st_gid):
            os.fchown(lock, owner.st_uid, owner.st_gid)
    with os.fdopen(lock, 'w') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        text = merge_settings(env.read_text(), template, version) if env.exists() else template
        text = select_image(text, image)
        env.touch(mode=0o600, exist_ok=True)
        env.chmod(0o600)
        env.write_text(text)
    subprocess.run([sys.executable, str(directory / 'backup-key.py'), str(env)], check=True)
    print(f'\nInstalled deployment files in {directory} (release {version}).')
    print(f'Edit {env} to configure Cloudflare Access and your tunnel before starting.')
    print(f'Then run sh "{directory}/manage" to launch. No services have been started.')


def main():
    cli = parser()
    args = cli.parse_args()
    for option in ('directory', 'repository', 'version', 'project_name', 'archive', 'volume'):
        if getattr(args, option) == '':
            fail('Missing value for --' + option.replace('_', '-'))
    if args.action == 'install':
        if args.backup != 'none' or args.foreground or args.pull or args.project_name or args.archive or args.volume:
            fail('install does not support startup, backup, or restore options')
        install(args)
    else:
        if args.directory is not None or args.repository is not None or args.version is not None or args.dry_run:
            fail('--directory, --repository, --version, and --dry-run are only supported by install')
        manage(args, Path(__file__).resolve().parent)


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)
    except (OSError, ValueError, KeyError) as error:
        sys.exit(str(error))
