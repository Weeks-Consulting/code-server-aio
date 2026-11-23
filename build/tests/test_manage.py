"""Check deployment management commands without starting containers."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "manage"


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Match the launcher's physical paths (macOS /var aliases /private/var).
        self.root = Path(self.temp.name).resolve()
        self.deployment = self.root / "deployment with spaces"
        self.deployment.mkdir()
        self.script = self.deployment / "manage"
        self.script.write_bytes(SCRIPT.read_bytes())
        (self.deployment / "manage.py").write_bytes((SCRIPT.parent / "manage.py").read_bytes())
        (self.deployment / "backup-key.py").write_bytes(
            (SCRIPT.parent / "build/tools/backup-key.py").read_bytes())
        (self.deployment / "restore-home.sh").write_bytes(
            (SCRIPT.parent / "build/tools/restore-home.sh").read_bytes())
        for name in (".env", "compose.yaml",
                     "compose.backup-common.yaml", "compose.backup.yaml", "compose.backup-local.yaml"):
            (self.deployment / name).touch()
        self.log = self.root / "arguments"
        docker = self.root / "docker"
        docker.write_text(
            '#!/usr/bin/env python3\nimport json, os, sys\n'
            'from pathlib import Path\n'
            'Path(os.environ["ARGUMENT_LOG"]).write_text(json.dumps(sys.argv[1:]))\n'
            'with open(os.environ["ARGUMENT_LOG"] + ".calls", "a") as log: log.write(json.dumps(sys.argv[1:]) + "\\n")\n'
            'Path(os.environ["ARGUMENT_LOG"] + ".env").write_text(json.dumps({k: os.environ.get(k) for k in ("BACKUP_LOCAL_UID", "BACKUP_LOCAL_GID", "CODE_SERVER_MEMORY_LIMIT")}))\n'
            'if sys.argv[1:] == ["info", "--format", "{{json .}}"]: print(json.dumps({"MemTotal": int(os.environ.get("TEST_HOST_MEMORY", "10737418240")), "Name": "docker-host.example"}))\n'
            'if sys.argv[1:4] == ["container", "ls", "--quiet"] and "status=running" not in sys.argv: print(os.environ.get("TEST_RUNNING_CONTAINERS", ""))\n'
            'if sys.argv[1:5] == ["volume", "ls", "--quiet", "--filter"]: print(os.environ.get("TEST_PROJECT_VOLUMES", ""))\n'
            'if sys.argv[1:] == ["volume", "ls", "--quiet"]: print(os.environ.get("TEST_ALL_VOLUMES", ""))\n'
            'if sys.argv[-3:] == ["config", "--format", "json"]:\n'
            '    args = sys.argv[1:]\n'
            '    if args[args.index("-f") + 1] == "-":\n'
            f'        os.execv({shutil.which("docker")!r}, sys.argv)\n'
            '    model = {"name": args[args.index("--project-name") + 1] if "--project-name" in args else "example", "services": {"code-server": {"image": os.environ.get("CODE_SERVER_IMAGE", "test"), "mem_limit": os.environ.get("CODE_SERVER_MEMORY_LIMIT"), "hostname": os.environ.get("CODE_SERVER_HOSTNAME")}}}\n'
            '    print(os.environ.get("TEST_MODEL", json.dumps(model)))\n'
            'if sys.argv[-4:] == ["ps", "--all", "--quiet", "code-server"]: print("workspace-container")\n'
            'if sys.argv[-4:-1] == ["ps", "--all", "--quiet"] and sys.argv[-1] in ("cloudflared", "backup"):\n'
            '    print(sys.argv[-1] + "-container")\n'
            'if sys.argv[1:] == ["inspect", "--format", "{{.Image}}", "workspace-container"]:\n'
            '    print("sha256:new-workspace-image" if Path(os.environ["ARGUMENT_LOG"] + ".created").exists() else "sha256:workspace-image")\n'
            'if sys.argv[1:4] == ["inspect", "--format", "{{.Image}}"]:\n'
            '    if sys.argv[-1] == "cloudflared-container": print("sha256:" + "a" * 64)\n'
            '    if sys.argv[-1] == "backup-container": print("sha256:" + "b" * 64)\n'
            'if sys.argv[1:5] == ["image", "inspect", "--format", "{{.Created}}"]:\n'
            '    print("2026-11-15T12:34:56Z")\n'
            'if sys.argv[1:5] == ["image", "inspect", "--format", "{{json .RepoTags}}"]: print(os.environ.get("TEST_OLD_IMAGE_TAGS", "null"))\n'
            'if sys.argv[1:3] == ["create", "--name"]: print("workspace-reservation")\n'
            'if sys.argv[1:4] == ["container", "ls", "--quiet"] and "status=running" in sys.argv: print(os.environ.get("TEST_BACKUP_SIDECAR", ""))\n'
            'if sys.argv[1:2] == ["exec"] and "flock" in sys.argv:\n'
            '    lock = os.environ.get("TEST_BACKUP_LOCK", "free")\n'
            '    if lock == "held": sys.exit(1)\n'
            '    if lock == "exec-error": sys.stderr.write("Error response from daemon: Container sidecar is restarting\\n"); sys.exit(1)\n'
            '    print("locked", flush=True); sys.stdin.read(); sys.exit(0)\n'
            'if sys.argv[1:4] == ["inspect", "--format", "{{.State.Status}}"]: print(os.environ.get("TEST_SIDECAR_STATE", "running"))\n'
            'if sys.argv[1:3] == ["image", "inspect"] and sys.argv[-1] in os.environ.get("TEST_MISSING_IMAGES", "").split(): sys.exit(1)\n'
            'if "create" in sys.argv and os.environ.get("FAIL_CREATE"): sys.exit(9)\n'
            'if sys.argv[-2:] == ["create", "--remove-orphans"] and "TEST_OLD_IMAGE_TAGS" in os.environ:\n'
            '    Path(os.environ["ARGUMENT_LOG"] + ".created").touch()\n'
            'if "pull" in sys.argv and os.environ.get("TEST_PULL_SLEEP"):\n'
            '    import time; time.sleep(float(os.environ["TEST_PULL_SLEEP"]))\n'
            'sys.exit(int(os.environ.get("DOCKER_EXIT", "0")))\n'
        )
        docker.chmod(0o755)
        self.env = {**os.environ, "PATH": f'{self.root}:{os.environ["PATH"]}',
                    "ARGUMENT_LOG": str(self.log)}

    def run_launcher(self, *args):
        return subprocess.run(
            ["sh", str(self.script), *args],
            env=self.env, cwd=self.root, capture_output=True, text=True,
        )

    def calls(self):
        return [json.loads(line) for line in Path(str(self.log) + '.calls').read_text().splitlines()]

    def snapshot(self):
        return json.loads((self.deployment / '.manage.compose.json').read_text())

    def configure(self, *options):
        result = self.run_launcher('configure', *options)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_modes(self):
        for mode, overlay in (("none", None), ("s3", "compose.backup.yaml"),
                              ("local", "compose.backup-local.yaml")):
            with self.subTest(mode=mode):
                self.configure('--backup', mode)
                render = next(call for call in reversed(self.calls()) if call[-3:] == ['config', '--format', 'json'])
                self.assertIn(str(self.deployment / 'compose.yaml'), render)
                if overlay:
                    self.assertIn(str(self.deployment / overlay), render)
                self.assertEqual(self.snapshot()['x-manage-backup'], mode)
                self.assertEqual(self.calls()[-1][-2:], ['create', '--remove-orphans'])
                self.assertEqual((self.deployment / '.manage.compose.json').stat().st_mode & 0o777, 0o600)

    def test_start_stop_use_saved_configuration(self):
        self.configure('--backup', 'local', '--project-name', 'custom')
        saved = (self.deployment / '.manage.compose.json').read_bytes()
        self.env['COMPOSE_PROJECT_NAME'] = 'unrelated-project'
        # Lifecycle commands work after source configuration changes or disappears.
        (self.deployment / '.env').unlink()
        (self.deployment / 'compose.yaml').unlink()
        for action in ('stop', 'start'):
            before = len(self.calls())
            result = self.run_launcher(action)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = self.calls()[before:]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][-1], action)
            self.assertEqual(calls[0][calls[0].index('--project-name') + 1], 'custom')
            self.assertIn(str(self.deployment / '.manage.compose.json'), calls[0])
            self.assertEqual((self.deployment / '.manage.compose.json').read_bytes(), saved)
        self.assertEqual(self.snapshot()['name'], 'custom')
        self.assertEqual(self.snapshot()['x-manage-backup'], 'local')

    def test_start_pull_refreshes_saved_images_without_reconfiguration(self):
        for image in ('ghcr.io/example/app:1.2.3', 'ghcr.io/example/app@sha256:' + 'a' * 64):
            with self.subTest(image=image):
                self.env['TEST_MODEL'] = json.dumps({'name': 'custom', 'services': {
                    'code-server': {'image': image}}})
                self.configure('--backup', 'local', '--project-name', 'custom')
                saved = (self.deployment / '.manage.compose.json').read_bytes()
                # Refresh the saved image even if .env/Compose have since changed.
                (self.deployment / '.env').write_text('CODE_SERVER_IMAGE=other/app:changed\n')
                (self.deployment / 'compose.yaml').write_text('invalid: [\n')
                self.env['COMPOSE_PROJECT_NAME'] = 'unrelated'
                before = len(self.calls())
                result = self.run_launcher('start', '--pull')
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.calls()[before:]
                # Images are pulled visibly before any lock; create then reuses them.
                self.assertEqual(len(calls), 3)
                self.assertEqual(calls[0][-2:], ['pull', 'code-server'])
                self.assertEqual(calls[1][-1], 'create')
                self.assertEqual(calls[2][-1], 'start')
                for call in calls:
                    self.assertIn(str(self.deployment / '.manage.compose.json'), call)
                    self.assertEqual(call[call.index('--project-name') + 1], 'custom')
                    self.assertEqual(call[call.index('--env-file') + 1], '/dev/null')
                self.assertEqual((self.deployment / '.manage.compose.json').read_bytes(), saved)
                self.assertEqual(self.snapshot()['x-manage-backup'], 'local')

    def test_failed_start_pull_does_not_start_or_replace_saved_configuration(self):
        self.configure()
        saved = (self.deployment / '.manage.compose.json').read_bytes()
        self.env['FAIL_CREATE'] = '1'
        before = len(self.calls())
        result = self.run_launcher('start', '--pull')
        self.assertEqual(result.returncode, 9, result.stderr)
        self.assertEqual(len(self.calls()[before:]), 2)
        self.assertEqual(self.calls()[-2][-2:], ['pull', 'code-server'])
        self.assertEqual(self.calls()[-1][-1], 'create')
        self.assertEqual((self.deployment / '.manage.compose.json').read_bytes(), saved)

    def test_pull_is_limited_to_start_and_configure(self):
        for action in ('stop', 'upgrade', 'install', 'logs', 'backup'):
            with self.subTest(action=action):
                result = self.run_launcher(action, '--pull')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('--pull is only supported by configure and start', result.stderr)
                self.assertFalse(self.log.exists())

    def test_configure_preserves_selection_and_can_disable_backups(self):
        self.configure('--backup', 'local', '--project-name', 'custom')
        self.configure()
        self.assertEqual(self.snapshot()['name'], 'custom')
        self.assertEqual(self.snapshot()['x-manage-backup'], 'local')
        self.configure('--backup', 'none')
        self.assertEqual(self.snapshot()['x-manage-backup'], 'none')

    def test_configure_rejects_renaming_a_project_with_resources(self):
        self.configure('--project-name', 'existing')
        path = self.deployment / '.manage.compose.json'
        saved = self.snapshot()
        saved['volumes'] = {'code-server-home': {'name': 'existing-home'}}
        path.write_text(json.dumps(saved))
        original = path.read_bytes()
        dotenv = self.deployment / '.env'
        original_env = dotenv.read_bytes()
        label = 'label=com.docker.compose.project=existing'
        checks = [
            ['container', 'ls', '--quiet', '--filter', label],
            ['volume', 'ls', '--quiet', '--filter', label],
            ['volume', 'ls', '--quiet'],
        ]
        for setting, output, message, expected_calls in (
                ('TEST_RUNNING_CONTAINERS', 'tunnel-container', 'running containers', checks[:1]),
                ('TEST_PROJECT_VOLUMES', 'previously-used-volume', 'Docker volumes', checks[:2]),
                ('TEST_ALL_VOLUMES', 'unrelated\nexisting-home', 'Docker volumes', checks)):
            with self.subTest(setting=setting):
                self.env[setting] = output
                before = len(self.calls())
                result = self.run_launcher('configure', '--project-name', 'replacement',
                                           '--backup', 'local', '--pull', '--start')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f'from existing to replacement while existing has {message}', result.stderr)
                self.assertEqual(self.calls()[before:], expected_calls)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(dotenv.read_bytes(), original_env)
                self.env.pop(setting)

    def test_configure_can_rename_an_empty_project(self):
        self.configure('--project-name', 'existing')
        path = self.deployment / '.manage.compose.json'
        saved = self.snapshot()
        saved['volumes'] = {'code-server-home': {'name': 'existing-home'}}
        path.write_text(json.dumps(saved))
        self.env.update(COMPOSE_PROJECT_NAME='unrelated', TEST_ALL_VOLUMES='existing-home-copy\nother')
        self.configure('--project-name', 'replacement')
        self.assertEqual(self.snapshot()['name'], 'replacement')

    def test_configure_keeps_project_name_when_docker_listing_fails(self):
        self.configure('--project-name', 'existing')
        self.env['DOCKER_EXIT'] = '7'
        before = len(self.calls())
        result = self.run_launcher('configure', '--project-name', 'replacement')
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.snapshot()['name'], 'existing')
        self.assertEqual(self.calls()[before:], [
            ['container', 'ls', '--quiet', '--filter', 'label=com.docker.compose.project=existing'],
        ])

    def test_configure_start_is_explicit(self):
        self.configure()
        self.assertEqual(self.calls()[-1][-2:], ['create', '--remove-orphans'])
        self.configure('--start')
        self.assertEqual(self.calls()[-2][-2:], ['create', '--remove-orphans'])
        self.assertEqual(self.calls()[-1][-1], 'start')

    def test_start_tags_pinned_sidecar_images_by_creation_date_and_id(self):
        cloudflared = 'cloudflare/cloudflared:latest@sha256:' + 'c' * 64
        backup = 'offen/docker-volume-backup:v2@sha256:' + 'd' * 64
        services = {
            'code-server': {'image': 'test'},
            'cloudflared': {'image': cloudflared},
            'backup': {'image': backup},
        }
        self.env['TEST_MODEL'] = json.dumps({'name': 'example', 'services': services})
        self.configure()
        self.assertFalse(any(call[:2] == ['image', 'tag'] for call in self.calls()))

        for action in (('configure', '--start'), ('start',)):
            with self.subTest(action=action):
                before = len(self.calls())
                result = self.run_launcher(*action)
                self.assertEqual(result.returncode, 0, result.stderr)
                calls = self.calls()[before:]
                backup_start = next(i for i, call in enumerate(calls) if call[-2:] == ['start', 'backup'])
                self.assertEqual([call for call in calls[backup_start + 1:] if call[:2] == ['image', 'tag']], [
                    ['image', 'tag', 'sha256:' + 'a' * 64, 'cloudflare/cloudflared:2026-11-15-aaaaaaaaaaaa'],
                    ['image', 'tag', 'sha256:' + 'b' * 64, 'offen/docker-volume-backup:2026-11-15-bbbbbbbbbbbb'],
                ])
                self.assertEqual(self.snapshot()['services'], services)

    def test_configure_waits_for_deployment_files_before_rendering(self):
        dotenv = self.deployment / '.env'
        compose_file = self.deployment / 'compose.yaml'
        snapshot = self.deployment / '.manage.compose.json'
        with (self.deployment / '.env.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with subprocess.Popen(['sh', str(self.script), 'configure'], env=self.env, cwd=self.root,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
                try:
                    deadline = time.monotonic() + 10
                    while not snapshot.with_name('.manage.compose.json.lock').exists():
                        self.assertIsNone(process.poll())
                        self.assertLess(time.monotonic(), deadline)
                        time.sleep(0.01)
                    # Configure must wait before reading deployment files or
                    # invoking Docker.
                    with self.assertRaises(subprocess.TimeoutExpired):
                        process.wait(timeout=0.25)
                    self.assertFalse(self.log.exists())
                    self.assertFalse(snapshot.exists())
                    dotenv.write_text('CODE_SERVER_IMAGE=test:new\n')
                    compose_file.write_text('services: {}\n')
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    _, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0, stderr)
                    self.assertTrue(snapshot.exists())
                finally:
                    if process.poll() is None:
                        process.kill()

    def test_failed_create_keeps_saved_configuration_and_cleans_temporary_file(self):
        self.configure('--backup', 'none')
        saved = (self.deployment / '.manage.compose.json').read_bytes()
        self.env['FAIL_CREATE'] = '1'
        result = self.run_launcher('configure', '--backup', 'local', '--start')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.deployment / '.manage.compose.json').read_bytes(), saved)
        self.assertEqual(list(self.deployment.glob('.manage.*.json')), [self.deployment / '.manage.compose.json'])
        self.assertNotEqual(self.calls()[-1][-1], 'start')

    def test_configure_saves_docker_host_name(self):
        self.env['CODE_SERVER_HOSTNAME'] = 'client-host.example'
        self.configure('--backup', 'none')
        saved = json.loads((self.deployment / '.manage.compose.json').read_text())
        self.assertEqual(saved['services']['code-server']['hostname'], 'docker-host.example')

    def test_local_backup_owner(self):
        self.env.update(SUDO_UID='1234', SUDO_GID='5678')
        self.configure('--backup', 'local')
        owner = json.loads(Path(str(self.log) + '.env').read_text())
        self.assertEqual(owner, {'BACKUP_LOCAL_UID': str(os.getuid()),
                                'BACKUP_LOCAL_GID': str(os.getgid()),
                                'CODE_SERVER_MEMORY_LIMIT': '8589934592'})

    def test_logs(self):
        self.configure('--backup', 'local', '--project-name', 'custom')
        for follow in ((), ('-f',), ('--follow',)):
            result = self.run_launcher('logs', *follow)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = json.loads(self.log.read_text())
            self.assertEqual(args[args.index('logs'):], ['logs', '--follow'] if follow else ['logs'])
        self.env['DOCKER_EXIT'] = '7'
        self.assertEqual(self.run_launcher('-f', 'logs').returncode, 7)

    def test_follow_requires_logs(self):
        for action in ('install', 'configure', 'start', 'stop', 'upgrade', 'backup'):
            result = self.run_launcher(action, '-f')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('only supported by the logs command', result.stderr)
            self.assertFalse(self.log.exists())

    def test_workspace_memory_uses_docker_host_and_rejects_invalid_totals(self):
        self.env['TEST_HOST_MEMORY'] = '1000000000'
        self.configure()
        self.assertEqual(self.snapshot()['services']['code-server']['mem_limit'], '800000000')
        saved = (self.deployment / '.manage.compose.json').read_bytes()
        for value in ('0', '-1', 'invalid'):
            self.env['TEST_HOST_MEMORY'] = value
            result = self.run_launcher('configure')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((self.deployment / '.manage.compose.json').read_bytes(), saved)

    def test_release_warning_is_best_effort(self):
        # Exercise the full launcher, replacing only the upstream network response.
        release_called = self.root / 'release-called'
        (self.root / "sitecustomize.py").write_text(
            "import io, os, pathlib, urllib.request\n"
            "def release(self, request, timeout):\n"
            "    assert timeout == 2\n"
            "    assert request.full_url == 'https://api.github.com/repos/weeks-consulting/code-server-aio/releases/latest'\n"
            f"    pathlib.Path({str(release_called)!r}).touch()\n"
            "    if os.environ.get('TEST_OFFLINE'): raise OSError('offline')\n"
            "    return io.BytesIO(os.environ.get('TEST_RELEASE', '{\"tag_name\": \"v9.0.0\"}').encode())\n"
            "urllib.request.OpenerDirector.open = release\n")
        self.env["PYTHONPATH"] = str(self.root)
        for tag, offline, warn in (("1.0.0", False, True), ("9.0.0", False, False),
                                   ("1.0.0", True, False), ("latest", False, False)):
            with self.subTest(tag=tag, offline=offline):
                release_called.unlink(missing_ok=True)
                self.env["TEST_MODEL"] = json.dumps({"services": {"code-server": {
                    "image": "ghcr.io/weeks-consulting/code-server-aio:" + tag}}})
                if offline:
                    self.env["TEST_OFFLINE"] = "1"
                else:
                    self.env.pop("TEST_OFFLINE", None)
                result = self.run_launcher("configure", "--pull")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(release_called.exists(), tag != "latest")
                self.assertEqual("differs from latest release" in result.stderr, warn)
                self.assertEqual(self.calls()[-2][-2:], ["pull", "code-server"])
                self.assertEqual(self.calls()[-1][-2:], ["create", "--remove-orphans"])

        # Invalid or non-stable upstream responses must not warn or block startup.
        self.env.pop("TEST_OFFLINE", None)
        self.env["TEST_MODEL"] = json.dumps({"services": {"code-server": {
            "image": "ghcr.io/weeks-consulting/code-server-aio:1.0.0"}}})
        for release in ({"tag_name": "invalid"}, {"tag_name": "v9.0.0", "draft": True},
                        {"tag_name": "v9.0.0", "prerelease": True}):
            with self.subTest(release=release):
                release_called.unlink(missing_ok=True)
                self.env["TEST_RELEASE"] = json.dumps(release)
                result = self.run_launcher("configure", "--pull")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(release_called.exists())
                self.assertNotIn("differs from latest release", result.stderr)
                self.assertEqual(self.calls()[-2][-2:], ["pull", "code-server"])

    def test_start_pull_warns_when_pinned_tag_is_no_longer_rebuilt(self):
        (self.root / "sitecustomize.py").write_text(
            "import io, os, urllib.request\n"
            "def release(self, request, timeout):\n"
            "    assert timeout == 2\n"
            "    if os.environ.get('TEST_OFFLINE'): raise OSError('offline')\n"
            "    return io.BytesIO(b'{\"tag_name\": \"v9.0.0\"}')\n"
            "urllib.request.OpenerDirector.open = release\n")
        self.env["PYTHONPATH"] = str(self.root)
        saved = self.deployment / '.manage.compose.json'
        for tag, offline, warn in (("1.0.0", False, True), ("9.0.0", False, False), ("1.0.0", True, False)):
            with self.subTest(tag=tag, offline=offline):
                saved.write_text(json.dumps({
                    'name': 'example',
                    'services': {'code-server': {
                        'image': 'ghcr.io/weeks-consulting/code-server-aio:' + tag,
                        'volumes': [{'type': 'volume', 'source': 'code-server-home', 'target': '/home'}]}},
                    'volumes': {'code-server-home': {'name': 'example_code-server-home'}}}))
                if offline:
                    self.env["TEST_OFFLINE"] = "1"
                else:
                    self.env.pop("TEST_OFFLINE", None)
                result = self.run_launcher("start", "--pull")
                # The warning is advisory: start still pulls, recreates, and starts.
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("differs from latest release v9.0.0" in result.stderr, warn)
                if warn:
                    self.assertIn("manage upgrade", result.stderr)
                calls = self.calls()
                self.assertEqual(calls[-2][0], 'compose')
                self.assertEqual(calls[-2][-1], 'start')
                self.assertEqual(calls[-1][:2], ['rm', 'workspace-reservation'])

    def test_upgrade_needs_no_github_account(self):
        # Refresh details and failure paths are covered by test_install's upgrade tests.
        repository = SCRIPT.parent
        sources = ["compose.yaml", "compose.backup-common.yaml", "compose.backup.yaml",
                   "compose.backup-local.yaml", "manage", "manage.py", ".env.example",
                   "build/tools/restore-home.sh", "build/tools/backup-key.py"]
        import gzip
        import io
        import tarfile
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for name in sources:
                archive.add(repository / name, arcname=Path(name).name)
        (self.root / "code-server-aio.tar.gz").write_bytes(gzip.compress(buffer.getvalue(), mtime=0))
        (self.root / "SHA256SUMS").write_text(
            hashlib.sha256((self.root / "code-server-aio.tar.gz").read_bytes()).hexdigest() + "  code-server-aio.tar.gz\n")
        # Model a regular user even when CI runs as root: root-only destination
        # checks reject the world-writable temp directory and are tested in test_install.
        (self.root / "sitecustomize.py").write_text(
            "import io, json, os, pathlib, urllib.request\n"
            "os.geteuid = lambda: 1000\n"
            f"root = pathlib.Path({str(self.root)!r})\n"
            "def respond(self, request, timeout):\n"
            "    assert 'Authorization' not in request.headers\n"
            "    url = request.full_url\n"
            "    if url == 'https://api.github.com/repos/example/fork/releases/latest':\n"
            "        return io.BytesIO(json.dumps({'tag_name': 'v9.0.0-1',\n"
            "            'assets': [{'name': 'SHA256SUMS'}, {'name': 'code-server-aio.tar.gz'}],\n"
            "            'html_url': 'https://github.com/Example/Fork/releases/tag/v9.0.0-1'}).encode())\n"
            "    prefix = 'https://github.com/Example/Fork/releases/download/v9.0.0-1/'\n"
            "    assert url.startswith(prefix), url\n"
            "    return io.BytesIO((root / url[len(prefix):]).read_bytes())\n"
            "urllib.request.OpenerDirector.open = respond\n")
        # No GitHub CLI or login; package managers must never run.
        for name, body in {"gh": f"touch {str(self.root / 'gh-called')!r}; exit 99",
                           "apt-get": "exit 99", "brew": "exit 99", "sudo": "exit 99"}.items():
            (self.root / name).write_text("#!/bin/sh\n" + body + "\n")
            (self.root / name).chmod(0o755)
        self.env["PYTHONPATH"] = str(self.root)
        self.env.pop("GH_TOKEN", None)
        self.env.pop("GITHUB_TOKEN", None)
        dotenv = self.deployment / ".env"
        dotenv.write_text("# Keep my settings\n"
                          "export CODE_SERVER_IMAGE='ghcr.io/example/fork:1.0.0'\n"
                          "SECRET=$(never-execute-this)\n")
        previous_image = 'ghcr.io/example/fork:1.0.0'
        saved = self.deployment / '.manage.compose.json'
        saved.write_text(json.dumps({'name': 'previous-project', 'services': {'code-server': {'image': previous_image}}}))
        self.env['TEST_OLD_IMAGE_TAGS'] = json.dumps([previous_image])
        self.env['CODE_SERVER_IMAGE'] = 'unrelated-shell-override'
        removal = ['image', 'rm', '--no-prune', 'sha256:workspace-image']
        self.env['FAIL_CREATE'] = '1'
        failed = self.run_launcher("upgrade")
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('deployment could not be applied', failed.stderr)
        self.assertEqual(self.snapshot()['services']['code-server']['image'], previous_image)
        self.assertNotIn(removal, self.calls())
        del self.env['FAIL_CREATE']
        before = len(self.calls())
        result = self.run_launcher("upgrade")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = dotenv.read_text()
        self.assertIn("# Keep my settings\n", text)
        self.assertIn("SECRET=$(never-execute-this)\n", text)
        self.assertIn("CODE_SERVER_IMAGE=ghcr.io/example/fork:9.0.0-1\n", text)
        self.assertEqual(dotenv.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.deployment / "compose.yaml").read_bytes(), (repository / "compose.yaml").read_bytes())
        self.assertEqual(self.snapshot()['name'], 'previous-project')
        self.assertEqual(self.snapshot()['services']['code-server']['image'], 'ghcr.io/example/fork:9.0.0-1')
        calls = self.calls()[before:]
        pull = next(i for i, call in enumerate(calls) if call[-2:] == ['pull', 'code-server'])
        create = next(i for i, call in enumerate(calls) if call[-2:] == ['create', '--remove-orphans'])
        start = next(i for i, call in enumerate(calls) if call[-1] == 'start')
        self.assertLess(pull, create)
        self.assertLess(create, start)
        self.assertEqual(self.calls()[-1], removal)
        self.assertFalse((self.root / "gh-called").exists())

    def write_backup_configuration(self):
        saved = self.deployment / '.manage.compose.json'
        saved.write_text(json.dumps({
            'name': 'example', 'x-manage-backup': 'local',
            'services': {'code-server': {'image': 'test'}, 'backup': {'image': 'offen'}},
            'volumes': {'code-server-home': {'name': 'example_code-server-home'}},
        }))
        self.env['TEST_MODEL'] = saved.read_text()
        self.env['TEST_BACKUP_SIDECAR'] = 'sidecar'

    def test_lifecycle_holds_running_backup_lock(self):
        self.write_backup_configuration()
        for action in ('stop', 'start'):
            result = self.run_launcher(action)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(['exec', '-i', 'sidecar', 'flock', '-n', '/var/lock/dockervolumebackup.lock',
                           '/bin/sh', '-ec', 'echo locked; cat >/dev/null'], self.calls())

    def test_running_backup_blocks_lifecycle(self):
        self.write_backup_configuration()
        self.env['TEST_BACKUP_LOCK'] = 'held'
        for action in (('stop',), ('start',), ('configure', '--backup', 'none')):
            result = self.run_launcher(*action)
            self.assertNotEqual(result.returncode, 0, action)
            self.assertIn('Could not acquire the backup lock for example: a backup is running', result.stderr)
            self.assertFalse(any(call[-1:] == ['stop'] and call[0] == 'compose' for call in self.calls()), action)

    def test_restarting_backup_sidecar_does_not_block_lifecycle(self):
        # A sidecar that began crash-looping after it was listed cannot hold the lock.
        self.write_backup_configuration()
        self.env.update(TEST_BACKUP_LOCK='exec-error', TEST_SIDECAR_STATE='restarting')
        for action in (('stop',), ('start',), ('configure', '--backup', 'none')):
            result = self.run_launcher(*action)
            self.assertEqual(result.returncode, 0, (action, result.stderr))
            self.assertIn('Backup service for example is restarting; continuing without its backup lock.',
                          result.stderr)
        compose = [call for call in self.calls() if call[0] == 'compose']
        self.assertTrue(any(call[-1] == 'stop' for call in compose))

    def test_backup_exec_error_on_running_sidecar_reports_docker_error(self):
        self.write_backup_configuration()
        self.env['TEST_BACKUP_LOCK'] = 'exec-error'
        result = self.run_launcher('stop')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('backup container sidecar is running', result.stderr)
        self.assertIn('Container sidecar is restarting', result.stderr)
        self.assertNotIn('a backup is running', result.stderr)

    def test_configure_rejects_invalid_backup_id_before_changing_containers(self):
        self.env['TEST_MODEL'] = json.dumps({
            'name': 'example',
            'services': {'code-server': {'image': 'test'},
                         'backup': {'image': 'offen', 'environment': {'BACKUP_ID': 'My-Host'}}},
        })
        result = self.run_launcher('configure', '--backup', 'local')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BACKUP_ID must be 1 to 64 lowercase letters", result.stderr)
        self.assertIn("'My-Host'", result.stderr)
        self.assertFalse(any('create' in call for call in self.calls()))
        self.assertFalse((self.deployment / '.manage.compose.json').exists())

    def test_start_without_pull_skips_network(self):
        network_called = self.root / 'network-called'
        (self.root / "sitecustomize.py").write_text(
            "import pathlib, urllib.request\n"
            "def fail(*args, **kwargs):\n"
            f"    pathlib.Path({str(network_called)!r}).touch()\n"
            "    raise OSError('offline')\n"
            "urllib.request.OpenerDirector.open = fail\n")
        self.env['PYTHONPATH'] = str(self.root)
        saved = self.deployment / '.manage.compose.json'
        volume_name = 'example_code-server-home'
        saved.write_text(json.dumps({
            'name': 'example',
            'services': {'code-server': {
                'image': 'ghcr.io/example/app:1.0.0',
                'volumes': [{'type': 'volume', 'source': 'code-server-home', 'target': '/home'}],
            }},
            'volumes': {'code-server-home': {'name': volume_name}},
        }))

        result = self.run_launcher("start")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(network_called.exists())
        compose = ['compose', '--project-directory', str(self.deployment), '--env-file', '/dev/null',
                   '-f', str(saved), '--project-name', 'example']
        reservation = 'code-server-restore-' + hashlib.sha256(volume_name.encode()).hexdigest()[:24]
        calls = self.calls()
        owner_label = calls[2][calls[2].index('--pull') - 1]
        self.assertRegex(owner_label, r'^code-server\.lifecycle-owner=[0-9a-f]{32}$')
        self.assertEqual(calls, [
            compose + ['ps', '--all', '--quiet', 'code-server'],
            ['inspect', '--format', '{{.Image}}', 'workspace-container'],
            ['create', '--name', reservation, '--network', 'none',
             '--label', 'code-server.lifecycle-reservation=true', '--label', owner_label,
             '--pull', 'never', '--entrypoint', '/bin/false', 'sha256:workspace-image'],
            ['container', 'ls', '--quiet', '--filter', 'label=com.docker.compose.project=example',
             '--filter', 'label=com.docker.compose.service=backup',
             '--filter', 'label=com.docker.compose.oneoff=False', '--filter', 'status=running'],
            compose + ['start'],
            ['rm', 'workspace-reservation'],
        ])

    def interrupt_lifecycle(self, operation, phase, signum, owner='ours', repeat=False):
        volume = 'lifecycle-home'
        (self.deployment / '.manage.compose.json').write_text(json.dumps({
            'name': 'example',
            'services': {'code-server': {'image': 'test'}},
            'volumes': {'code-server-home': {'name': volume}},
        }))
        self.env['TEST_MODEL'] = (self.deployment / '.manage.compose.json').read_text()
        docker = self.root / 'docker'
        docker.rename(self.root / 'docker-fixture')
        docker.write_text('#!/usr/bin/env python3\n' + textwrap.dedent('''\
            import json, os, signal, sys, time
            from pathlib import Path

            args = sys.argv[1:]
            root = Path(os.environ['ARGUMENT_LOG']).parent
            reservation = root / 'reservation'
            phase = os.environ['TEST_PAUSE_PHASE']

            def pause():
                (root / 'paused').touch()
                if phase != 'release':
                    time.sleep(60)
                else:
                    while not (root / 'continue').exists():
                        time.sleep(0.01)

            if args[:2] == ['create', '--name']:
                owner = next(arg.split('=', 1)[1] for arg in args if arg.startswith('code-server.lifecycle-owner='))
                if os.environ['TEST_RESERVATION_OWNER'] == 'other':
                    owner = 'another-invocation'
                reservation.write_text(owner)
                if phase == 'acquire':
                    pause()
                print('workspace-reservation')
            elif args[:3] == ['inspect', '--type', 'container']:
                if os.environ['TEST_RESERVATION_OWNER'] == 'unresolved':
                    sys.exit(1)
                print(json.dumps({'Id': 'workspace-reservation',
                                  'Config': {'Labels': {'code-server.lifecycle-owner': reservation.read_text()}}}))
            elif args == ['rm', 'workspace-reservation']:
                if phase == 'release':
                    pause()
                reservation.unlink()
            elif args[0] == 'compose' and args[-1] == 'start' and phase in ('start', 'start-stuck'):
                if phase == 'start-stuck':
                    for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
                        signal.signal(signum, signal.SIG_IGN)
                pause()
            else:
                os.execv(root / 'docker-fixture', [str(root / 'docker-fixture'), *args])
            '''))
        docker.chmod(0o755)
        env = {**self.env, 'TEST_PAUSE_PHASE': phase, 'TEST_RESERVATION_OWNER': owner}
        with subprocess.Popen(['sh', str(self.script), *operation], env=env, cwd=self.root,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                deadline = time.monotonic() + 10
                while not (self.root / 'paused').exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        self.fail('Lifecycle command did not reach ' + phase)
                    time.sleep(0.01)
                process.send_signal(signum)
                if repeat:
                    time.sleep(0.5)
                    self.assertIsNone(process.poll())
                    process.send_signal(signum)
                if phase == 'release':
                    # The cleanup subprocess should be allowed to finish.
                    time.sleep(0.1)
                    (self.root / 'continue').touch()
                _, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 128 + signum, stderr)
            finally:
                if process.poll() is None:
                    process.kill()
        name = 'code-server-restore-' + hashlib.sha256(volume.encode()).hexdigest()[:24]
        return name, stderr

    def test_interrupted_acquisition_removes_its_own_reservation(self):
        self.interrupt_lifecycle(('configure', '--start'), 'acquire', signal.SIGINT)
        self.assertFalse((self.root / 'reservation').exists())
        self.assertFalse(any('create' in call for call in self.calls()))

    def test_interrupted_acquisition_preserves_another_reservation(self):
        self.interrupt_lifecycle(('start',), 'acquire', signal.SIGTERM, owner='other')
        self.assertEqual((self.root / 'reservation').read_text(), 'another-invocation')

    def test_interrupted_acquisition_reports_an_unverified_reservation(self):
        name, stderr = self.interrupt_lifecycle(('start',), 'acquire', signal.SIGHUP, owner='unresolved')
        self.assertTrue((self.root / 'reservation').exists())
        self.assertIn(f'Could not verify reservation {name}', stderr)

    def test_interrupted_compose_start_stops_cleanly_and_releases_reservation(self):
        # The signal is forwarded once; Compose exits by itself, so nothing is in flight.
        name, stderr = self.interrupt_lifecycle(('start',), 'start', signal.SIGTERM)
        self.assertFalse((self.root / 'reservation').exists())
        self.assertNotIn('retained', stderr)

    def test_abandoned_compose_start_retains_reservation_for_inspection(self):
        # A second signal stops waiting for a client that did not exit.
        name, stderr = self.interrupt_lifecycle(('start',), 'start-stuck', signal.SIGTERM, repeat=True)
        self.assertTrue((self.root / 'reservation').exists())
        self.assertIn(f'Reservation {name} (workspace-reservation) retained', stderr)

    def test_configure_pulls_missing_images_before_reserving(self):
        self.env['TEST_MODEL'] = json.dumps({'name': 'example', 'services': {
            'code-server': {'image': 'workspace:1', 'volumes': [
                {'type': 'volume', 'source': 'code-server-home', 'target': '/home'}]},
            'cloudflared': {'image': 'tunnel:1'}, 'local': {'build': {'context': '.'}}},
            'volumes': {'code-server-home': {'name': 'home'}}})
        self.env['TEST_MISSING_IMAGES'] = 'workspace:1'
        self.configure()
        calls = self.calls()
        pulls = [index for index, call in enumerate(calls) if call[-2:] == ['pull', 'code-server']]
        reservation = next(index for index, call in enumerate(calls) if call[:2] == ['create', '--name'])
        self.assertEqual(len(pulls), 1)
        self.assertLess(pulls[0], reservation)
        self.assertEqual(calls[reservation][calls[reservation].index('--pull') + 1], 'never')
        # Present images are not refreshed without --pull.
        self.env.pop('TEST_MISSING_IMAGES')
        before = len(self.calls())
        self.configure()
        self.assertFalse(any('pull' in call for call in self.calls()[before:] if call[0] == 'compose'))

    def signal_during_pull(self, signum, ignore=False):
        self.env['TEST_MISSING_IMAGES'] = 'test'
        self.env['TEST_PULL_SLEEP'] = '3' if ignore else '30'
        Path(str(self.log) + '.calls').unlink(missing_ok=True)
        process = subprocess.Popen(
            ['sh', str(self.script), 'configure'], env=self.env, cwd=self.root,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            preexec_fn=(lambda: signal.signal(signum, signal.SIG_IGN)) if ignore else None)
        try:
            deadline = time.monotonic() + 15
            while not (Path(str(self.log) + '.calls').exists()
                       and any('pull' in call for call in self.calls())):
                self.assertLess(time.monotonic(), deadline, 'pull did not start')
                time.sleep(0.05)
            process.send_signal(signum)
            _, stderr = process.communicate(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        return process.returncode, stderr

    def test_hangup_or_termination_during_pull_removes_temporary_snapshot(self):
        for signum in (signal.SIGHUP, signal.SIGTERM):
            with self.subTest(signal=signum.name):
                returncode, stderr = self.signal_during_pull(signum)
                self.assertEqual(returncode, 128 + signum, stderr)
                # The interpolated model holds secrets; no copy may remain.
                self.assertEqual(list(self.deployment.glob('.manage.*.json')), [])

    def test_ignored_hangup_during_pull_is_preserved(self):
        # nohup ignores SIGHUP; manage must not turn it back into an exit.
        returncode, stderr = self.signal_during_pull(signal.SIGHUP, ignore=True)
        self.assertEqual(returncode, 0, stderr)
        self.assertEqual(list(self.deployment.glob('.manage.*.json')), [self.deployment / '.manage.compose.json'])

    def test_failed_reservation_create_does_not_report_unverified_reservation(self):
        (self.deployment / '.manage.compose.json').write_text(json.dumps({
            'name': 'example', 'services': {'code-server': {'image': 'test'}},
            'volumes': {'code-server-home': {'name': 'home'}}}))
        self.env['TEST_MODEL'] = (self.deployment / '.manage.compose.json').read_text()
        self.env['FAIL_CREATE'] = '1'
        result = self.run_launcher('configure')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Could not reserve workspace volume home', result.stderr)
        self.assertNotIn('Could not verify reservation', result.stderr)
        self.assertFalse(any(call[:3] == ['inspect', '--type', 'container'] for call in self.calls()))

    def test_interruption_does_not_abandon_reservation_removal(self):
        self.interrupt_lifecycle(('start',), 'release', signal.SIGINT)
        self.assertFalse((self.root / 'reservation').exists())

    def test_upgrade_rejects_custom_registry_and_launch_options(self):
        dotenv = self.deployment / ".env"
        original = "CODE_SERVER_IMAGE=example/custom:fixed\n"
        dotenv.write_text(original)
        for args in (("upgrade",), ("upgrade", "--pull"),
                     ("upgrade", "--backup", "local"), ("upgrade", "--project-name", "other")):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_launcher(*args).returncode, 0)
                self.assertEqual(dotenv.read_text(), original)
                self.assertFalse(self.log.exists())

    def test_invalid_options(self):
        for args in (("--backup", "invalid"), ("--backup",), ("--unknown",),
                     ("--directory", str(self.deployment)), ("--build",)):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_launcher(*args).returncode, 0)
                self.assertFalse(self.log.exists())

    def test_missing_files(self):
        for name, args in ((".env", ("configure",)), ("compose.backup.yaml", ("configure", "--backup", "s3"))):
            path = self.deployment / name
            path.unlink()
            self.assertNotEqual(self.run_launcher(*args).returncode, 0)
            self.assertFalse(self.log.exists())
            path.touch()

    def test_unreadable_env_explains_permissions(self):
        if os.geteuid() == 0:
            self.skipTest("root can read files regardless of their read permission bits")
        dotenv = self.deployment / ".env"
        dotenv.chmod(0)
        self.addCleanup(dotenv.chmod, 0o600)
        result = self.run_launcher("configure")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sudo", result.stderr)
        self.assertFalse(self.log.exists())

    def test_command_after_options(self):
        self.configure()
        result = self.run_launcher('--backup', 'local', 'configure', '--project-name', 'example')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot()['name'], 'example')
        result = self.run_launcher('start', 'stop')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unrecognized arguments', result.stderr)

    def test_help(self):
        result = self.run_launcher("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage:", result.stdout)
        self.assertIn('backup --run | --list | --restore --archive FILE_OR_URI [--volume NAME]', result.stdout)
        self.assertFalse(self.log.exists())

    def test_docker_exit_status(self):
        self.configure()
        self.env["DOCKER_EXIT"] = "17"
        self.assertEqual(self.run_launcher('start').returncode, 17)

    def test_command_is_required(self):
        self.configure()
        before = self.calls()
        for args in ((), ('--pull',)):
            with self.subTest(args=args):
                result = self.run_launcher(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn('required: COMMAND', result.stderr)
                self.assertEqual(self.calls(), before)

    def test_manual_backup(self):
        for mode in ("s3", "local"):
            with self.subTest(mode=mode):
                self.configure("--backup", mode)
                result = self.run_launcher("backup", "--run")
                self.assertEqual(result.returncode, 0, result.stderr)
                args = json.loads(self.log.read_text())
                self.assertEqual(args[-4:], ["exec", "-T", "backup", "backup"])

    def test_manual_backup_propagates_service_failure_without_fallback(self):
        self.configure("--backup", "local")
        self.env["DOCKER_EXIT"] = "17"
        result = self.run_launcher("backup", "--run")
        self.assertEqual(result.returncode, 17)
        self.assertEqual(json.loads(self.log.read_text())[-4:], ["exec", "-T", "backup", "backup"])

    def test_manual_backup_does_not_generate_a_key_unused_by_the_running_service(self):
        dotenv = self.deployment / ".env"
        self.configure("--backup", "local")
        dotenv.write_text("BACKUP_GPG_PASSPHRASE=\n")
        (self.deployment / ".env.lock").unlink()
        result = self.run_launcher("backup", "--run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dotenv.read_text(), "BACKUP_GPG_PASSPHRASE=\n")
        self.assertFalse((self.deployment / ".env.lock").exists())

    def test_backup_key_generated_and_preserved(self):
        dotenv = self.deployment / ".env"
        dotenv.write_text("BACKUP_GPG_PASSPHRASE=\n")
        result = self.run_launcher("configure", "--backup", "local")
        self.assertEqual(result.returncode, 0, result.stderr)
        contents = dotenv.read_text()
        self.assertRegex(contents, r"BACKUP_GPG_PASSPHRASE=[a-f0-9]{64}")
        result = self.run_launcher("backup", "--run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dotenv.read_text(), contents)

    def test_list_local_backups(self):
        self.configure("--backup", "local")
        result = self.run_launcher("backup", "--list")
        self.assertEqual(result.returncode, 0, result.stderr)
        args = json.loads(self.log.read_text())
        self.assertIn(str(self.deployment / ".manage.compose.json"), args)
        self.assertIn("find /archive", args[-1])
        self.assertIn("--no-deps", args)

    def test_restore_relative_to_caller(self):
        archive = self.root / "saved backup.tar.gz"
        archive.touch()
        (self.deployment / ".env").unlink()
        # This test checks launcher argument forwarding; the Docker integration
        # test exercises extraction, rollback encryption, and replacement.
        (self.deployment / "restore-home.sh").write_text(
            '#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGUMENT_LOG"\n')
        alias = self.root / "caller-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        for caller in (self.root, alias):
            with self.subTest(caller=caller):
                result = subprocess.run(
                    ["sh", str(self.script), "backup", "--restore", "--archive", archive.name,
                     "--volume", "new-home"],
                    env={**self.env, "PWD": str(caller)}, cwd=caller,
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                forwarded, volume = self.log.read_text().splitlines()
                self.assertTrue(Path(forwarded).is_absolute())
                # macOS /var is a symlink to /private/var; either spelling
                # must identify the archive relative to the caller's directory.
                self.assertEqual(Path(forwarded).resolve(), archive.resolve())
                self.assertEqual(volume, "new-home")

    def test_restore_reads_quoted_colon_form_key_from_dotenv(self):
        archive = self.root / "saved.tar.gz.gpg"
        archive.touch()
        (self.deployment / "restore-home.sh").write_text(
            '#!/bin/sh\nprintf "%s" "$BACKUP_GPG_PASSPHRASE" > "$ARGUMENT_LOG"\n')
        key = '0123456789abcdef' * 4
        (self.deployment / '.env').write_text(f"BACKUP_GPG_PASSPHRASE: '{key}'\n")
        env = {key: value for key, value in self.env.items() if key != "BACKUP_GPG_PASSPHRASE"}
        result = subprocess.run(
            ["sh", str(self.script), "backup", "--restore", "--archive", str(archive), "--volume", "home"],
            env=env, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text(), key)
        self.assertNotIn('differs from the key', result.stderr)
        # Warn, without printing either key, when backups use a different saved key.
        saved = self.deployment / '.manage.compose.json'
        for saved_key, warns in ((key, False), ('f' * 64, True)):
            with self.subTest(warns=warns):
                saved.write_text(json.dumps({'services': {'backup': {'environment': {'GPG_PASSPHRASE': saved_key}}}}))
                result = subprocess.run(
                    ["sh", str(self.script), "backup", "--restore", "--archive", str(archive), "--volume", "home"],
                    env=env, cwd=self.root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual('differs from the key' in result.stderr, warns)
                self.assertNotIn(saved_key, result.stderr)
                self.assertNotIn(key, result.stderr)

    def test_restore_requires_explicit_volume_when_home_is_a_bind_mount(self):
        (self.deployment / '.manage.compose.json').write_text(json.dumps({
            'services': {'code-server': {'volumes': [
                {'type': 'bind', 'source': '/srv/home', 'target': '/home'},
            ]}},
            'volumes': {'code-server-home': {'name': 'unused-home'}},
        }))
        result = self.run_launcher('backup', '--restore', '--archive', 'saved.tar.gz')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Use --volume NAME', result.stderr)
        self.assertFalse(self.log.exists())

    def test_restore_rejects_invalid_key_before_using_docker(self):
        archive = self.root / 'saved.tar.gz.gpg'
        archive.touch()
        self.env['BACKUP_GPG_PASSPHRASE'] = 'a' * 31 + '\n' + 'a' * 32
        result = self.run_launcher('backup', '--restore', '--archive', str(archive), '--volume', 'home')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('64 lowercase hexadecimal characters', result.stderr)
        self.assertFalse(self.log.exists())

    def test_missing_configuration_explains_setup(self):
        for args in (('start',), ('start', '--pull'), ('stop',), ('logs',), ('backup', '--run'),
                     ('backup', '--list'), ('backup', '--restore', '--archive', 'saved.tar.gz')):
            result = self.run_launcher(*args)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('manage configure --start', result.stderr)
            self.assertFalse(self.log.exists())

    def test_configuration_options_require_configure(self):
        for action in ('start', 'stop', 'logs', 'backup', 'upgrade', 'install'):
            for options in (('--backup', 'none'), ('--project-name', 'example'), ('--start',)):
                with self.subTest(action=action, options=options):
                    result = self.run_launcher(action, *options)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('only supported by configure', result.stderr)
                    self.assertFalse(self.log.exists())

    def test_backup_commands_require_selected_mode(self):
        self.configure()
        for option in ('--run', '--list'):
            before = len(self.calls())
            result = self.run_launcher('backup', option)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(len(self.calls()), before)
        self.configure('--backup', 's3')
        before = len(self.calls())
        result = self.run_launcher('backup', '--list')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Saved S3 backup settings are incomplete', result.stderr)
        self.assertEqual(len(self.calls()), before)

    def s3_fixture(self):
        (self.deployment / '.manage.compose.json').write_text(json.dumps({
            'name': 'example', 'x-manage-backup': 's3',
            'services': {
                'code-server': {'volumes': [
                    {'type': 'volume', 'source': 'workspace-home', 'target': '/home'},
                ]},
                'backup': {'environment': {
                    'AWS_ENDPOINT': 'account.eu.r2.cloudflarestorage.com',
                    'AWS_S3_BUCKET_NAME': 'store', 'AWS_S3_PATH': 'example/home/',
                    'AWS_ACCESS_KEY_ID': 'saved-id', 'AWS_SECRET_ACCESS_KEY': 'saved-secret',
                }},
            },
            'volumes': {'workspace-home': {'name': 'custom-docker-home'}},
        }))
        aws = self.root / 'aws'
        aws.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import json
            import os
            from pathlib import Path
            import sys

            args = sys.argv[1:]
            assert args[:4] == ['--region', 'auto', '--endpoint-url',
                                'https://account.eu.r2.cloudflarestorage.com']
            assert os.environ['AWS_ACCESS_KEY_ID'] == 'saved-id'
            assert os.environ['AWS_SECRET_ACCESS_KEY'] == 'saved-secret'
            assert 'AWS_PROFILE' not in os.environ
            assert 'BACKUP_GPG_PASSPHRASE' not in os.environ
            config = Path(os.environ['AWS_CONFIG_FILE'])
            assert 'addressing_style = path' in config.read_text()
            Path(os.environ['ARGUMENT_LOG'] + '.aws').write_text(str(config))
            if args[4] == 's3api':
                assert args[5:] == ['list-objects-v2', '--output', 'json', '--bucket',
                                    'store', '--prefix=example/home/']
                print(json.dumps({{'Contents': [
                    {{'Key': 'example/home/older.tar.gz', 'LastModified': '2026-01-01', 'Size': 12}},
                    {{'Key': 'example/home/notes.txt', 'LastModified': '2026-03-01', 'Size': 8}},
                    {{'Key': 'example/home/newer.tar.gz.gpg', 'LastModified': '2026-02-01', 'Size': 24}},
                ]}}))
            else:
                assert args[4:] == ['s3', 'cp', os.environ['TEST_S3_URI'], '-', '--only-show-errors']
                sys.stdout.buffer.write(Path(os.environ['TEST_S3_BYTES']).read_bytes())
                sys.exit(int(os.environ.get('TEST_S3_EXIT', '0')))
            """))
        aws.chmod(0o755)
        self.env.update(AWS_ACCESS_KEY_ID='ignored-id', AWS_PROFILE='ignored-profile')

    def test_list_s3_backups_uses_saved_settings(self):
        self.s3_fixture()
        result = self.run_launcher('backup', '--list')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line.split()[-1] for line in result.stdout.splitlines()], [
            's3://store/example/home/newer.tar.gz.gpg', 's3://store/example/home/older.tar.gz'])
        self.assertFalse(self.log.exists())
        self.assertFalse(Path(Path(str(self.log) + '.aws').read_text()).parent.exists())

    def test_s3_restore_checks_download_before_replacing(self):
        self.s3_fixture()
        payload = self.root / 'download-bytes'
        payload.write_bytes(b'archive bytes')
        docker = self.root / 'docker'
        docker.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import os
            from pathlib import Path
            import sys

            assert 'CODE_SERVER_AIO_S3_RESTORE_SETTINGS' not in os.environ
            args = sys.argv[1:]
            if args[:2] == ['volume', 'inspect']:
                assert args[2:] == ['custom-docker-home']
            if args[0] == 'create':
                print('owned-reservation')
            if args[0] == 'exec':
                script = args[-1]
                if 'tar -xzp' in script:
                    assert sys.stdin.buffer.read() == b'archive bytes'
                elif 'tar -czp' in script:
                    sys.stdout.buffer.write(b'rollback bytes')
                elif 'tar -tzf' in script:
                    assert sys.stdin.buffer.read() == b'rollback bytes'
                elif 'mv -t /restore' in script:
                    Path(os.environ['ARGUMENT_LOG']).touch()
            """))
        self.env.pop('BACKUP_GPG_PASSPHRASE', None)
        uri = 's3://store/old-prefix/an archive.tar.gz'
        self.env.update(TEST_S3_URI=uri, TEST_S3_BYTES=str(payload))
        args = ('backup', '--restore', '--archive', uri)
        rejected = self.run_launcher('backup', '--restore', '--archive',
                                     's3://wrong/an archive.tar.gz', '--volume', 'home')
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('configured bucket', rejected.stderr)
        self.assertFalse(Path(str(self.log) + '.aws').exists())
        self.env['TEST_S3_EXIT'] = '22'
        failed = self.run_launcher(*args)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('Restore stream failed', failed.stderr)
        self.assertFalse(self.log.exists(), 'a failed transfer must never reach replacement')
        self.assertFalse(list(self.deployment.glob('pre-restore.*')))
        self.env.pop('TEST_S3_EXIT')
        succeeded = self.run_launcher(*args)
        self.assertEqual(succeeded.returncode, 0, succeeded.stderr)
        self.assertTrue(self.log.exists())
        rollbacks = list(self.deployment.glob('pre-restore.*/original-home.tar.gz'))
        self.assertEqual(len(rollbacks), 1)
        self.assertEqual(rollbacks[0].read_bytes(), b'rollback bytes')
        self.assertFalse(list(self.deployment.glob('*.tar.gz')))
        self.assertFalse(Path(Path(str(self.log) + '.aws').read_text()).parent.exists())

    def test_invalid_commands(self):
        for args in (("backup",), ("backups", "--backup", "s3"), ("restore",),
                     ("backup", "--backup", "local", "--pull"),
                     ("stop", "--pull"), ("stop", "--foreground"),
                     ("stop", "--volume", "existing"),
                     ("start", "--volume", "existing"), ("unknown",)):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_launcher(*args).returncode, 0)
                self.assertFalse(self.log.exists())

    def test_backup_requires_one_action(self):
        for options in ((), ('--run', '--list'), ('--run', '--restore'),
                        ('--list', '--restore')):
            with self.subTest(options=options):
                result = self.run_launcher('backup', *options)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())

    def test_backup_action_options_require_backup(self):
        for action in ('install', 'configure', 'start', 'stop', 'logs', 'upgrade'):
            for option in ('--run', '--list', '--restore'):
                with self.subTest(action=action, option=option):
                    result = self.run_launcher(action, option)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('only supported by backup', result.stderr)
                    self.assertFalse(self.log.exists())

    def test_restore_options_require_restore_action(self):
        for args in (('backup', '--restore'),
                     ('backup', '--restore', '--volume', 'home'),
                     ('backup', '--run', '--archive', 'saved.tar.gz'),
                     ('backup', '--list', '--volume', 'home')):
            with self.subTest(args=args):
                result = self.run_launcher(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('backup --restore', result.stderr)
                self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
