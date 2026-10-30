"""Check key generation, retention, and safe handling of deployment dotenv files."""

import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class BackupKeyTests(unittest.TestCase):
    def setUp(self):
        # Root installer fixtures need trusted ancestors: /tmp is intentionally
        # rejected by the production guard even when the test directory is 0700.
        parent = pwd.getpwuid(0).pw_dir if os.geteuid() == 0 else None
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.env = self.directory / ".env"

    def initialize(self):
        return subprocess.run([sys.executable, str(ROOT / "build/tools/backup-key.py"), str(self.env)],
                              capture_output=True, text=True)

    def test_missing_and_empty_keys(self):
        for value in ("", "BACKUP_GPG_PASSPHRASE=\n", 'BACKUP_GPG_PASSPHRASE="" # empty\n',
                      "export BACKUP_GPG_PASSPHRASE = ''\n", "BACKUP_GPG_PASSPHRASE=  \n",
                      "BACKUP_GPG_PASSPHRASE=''# generate a key\n", 'BACKUP_GPG_PASSPHRASE=""# generate\n'):
            with self.subTest(value=value):
                self.env.write_text("OTHER=unchanged\n" + value)
                result = self.initialize()
                self.assertEqual(result.returncode, 0, result.stderr)
                contents = self.env.read_text()
                key = re.search(r"^BACKUP_GPG_PASSPHRASE=([a-f0-9]{64})$", contents, re.M)[1]
                self.assertIn("OTHER=unchanged\n", contents)
                self.assertEqual(self.env.stat().st_mode & 0o777, 0o600)
                self.assertNotIn(key, result.stdout + result.stderr)
                self.assertEqual(self.initialize().returncode, 0)
                self.assertEqual(self.env.read_text(), contents)

    def test_preserves_keys_compose_would_use(self):
        # Compose treats a leading '#' (even after spaces) as part of the value.
        for value in ("BACKUP_GPG_PASSPHRASE=#my-own-passphrase\n", "BACKUP_GPG_PASSPHRASE= # not a comment\n",
                      "BACKUP_GPG_PASSPHRASE='#quoted'# comment\n", 'BACKUP_GPG_PASSPHRASE="a\\"#b"\n',
                      "BACKUP_GPG_PASSPHRASE=key #comment\n", "BACKUP_GPG_PASSPHRASE='  '\n"):
            with self.subTest(value=value):
                self.env.write_text(value)
                result = self.initialize()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.env.read_text(), value)
                self.assertNotIn("Generated", result.stdout)

    def test_matches_compose_effective_values(self):
        if not shutil.which("docker") or subprocess.run(["docker", "compose", "version"],
                                                        capture_output=True).returncode:
            self.skipTest("Docker Compose is unavailable")
        spec = importlib.util.spec_from_file_location("backup_key", ROOT / "build/tools/backup-key.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        values = ["", "  ", "#abc", " # not a comment", "key #comment", "''# gen", '""# gen',
                  "'' # c", "'#quoted'# c", '"a\\"#b"', "'  '", "x#y"]
        self.env.write_text("".join(f"K{index}={value}\n" for index, value in enumerate(values)))
        compose = self.directory / "compose.yaml"
        compose.write_text("services:\n  t:\n    image: unused\n    environment:\n" + "".join(
            f"      K{index}: ${{K{index}}}\n" for index in range(len(values))))
        model = subprocess.run(["docker", "compose", "--project-directory", str(self.directory),
                                "--env-file", str(self.env), "-f", str(compose), "config", "--format", "json"],
                               capture_output=True, text=True, check=True)
        resolved = json.loads(model.stdout)["services"]["t"]["environment"]
        # Key generation depends only on whether Compose sees an empty value.
        for index, value in enumerate(values):
            with self.subTest(value=value):
                self.assertEqual(bool(module.effective_value(value)), bool(resolved.get(f"K{index}")))

    def test_preserves_custom_key_and_never_executes_dotenv(self):
        marker = self.directory / "must-not-exist"
        contents = f"OTHER=$(touch {marker})\nexport BACKUP_GPG_PASSPHRASE='custom passphrase $literal' # keep\n"
        self.env.write_text(contents)
        self.assertEqual(self.initialize().returncode, 0)
        self.assertEqual(self.env.read_text(), contents)
        self.assertFalse(marker.exists())

    def test_rejects_multiline_values_without_changing_file(self):
        for quote in ("'", '"'):
            for suffix in (quote + "\n", ""):
                contents = ("BACKUP_GPG_PASSPHRASE=keep-secret\nOTHER=" + quote +
                            "first line\nBACKUP_GPG_PASSPHRASE=\n" + suffix)
                with self.subTest(quote=quote, suffix=suffix):
                    self.env.write_text(contents)
                    self.env.chmod(0o644)
                    result = self.initialize()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Multiline or unterminated", result.stderr)
                    self.assertNotIn("keep-secret", result.stderr)
                    self.assertEqual(self.env.read_text(), contents)
                    self.assertEqual(self.env.stat().st_mode & 0o777, 0o644)

    def test_different_deployments_get_different_keys(self):
        self.env.touch()
        self.assertEqual(self.initialize().returncode, 0)
        first = self.env.read_text()
        self.env.write_text("")
        self.assertEqual(self.initialize().returncode, 0)
        self.assertNotEqual(first, self.env.read_text())

    def test_concurrent_initializers_preserve_one_key(self):
        self.env.write_text("OTHER=unchanged\nBACKUP_GPG_PASSPHRASE=\n")
        processes = [subprocess.Popen(
            [sys.executable, str(ROOT / "build/tools/backup-key.py"), str(self.env)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ) for _ in range(8)]
        outputs = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr)
            outputs.append(stdout)
        self.assertEqual(sum("Generated backup encryption key" in out for out in outputs), 1)
        contents = self.env.read_text()
        self.assertRegex(contents, r"BACKUP_GPG_PASSPHRASE=[a-f0-9]{64}")
        self.assertIn("OTHER=unchanged\n", contents)
        self.assertEqual(self.initialize().returncode, 0)
        self.assertEqual(self.env.read_text(), contents)

    def test_last_assignment_controls_key_generation(self):
        self.env.write_text("BACKUP_GPG_PASSPHRASE=old-key\nBACKUP_GPG_PASSPHRASE=\n")
        self.assertEqual(self.initialize().returncode, 0)
        keys = re.findall(r"^BACKUP_GPG_PASSPHRASE=([a-f0-9]{64})$", self.env.read_text(), re.M)
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])
        contents = "BACKUP_GPG_PASSPHRASE=\nBACKUP_GPG_PASSPHRASE=keep-this\n"
        self.env.write_text(contents)
        self.assertEqual(self.initialize().returncode, 0)
        self.assertEqual(self.env.read_text(), contents)

    def installer_fixture(self):
        # Serve the workspace's release files without any network requests.
        tools = self.directory / "bin"
        tools.mkdir()
        # Patch the Python HTTP boundary while exercising the real launcher.
        (tools / "sitecustomize.py").write_text(
            "import hashlib, io, json, os, pathlib, platform, subprocess, urllib.request, urllib.error\n"
            f"root = pathlib.Path({str(ROOT)!r})\n"
            f"tools = pathlib.Path({str(self.directory / 'bin')!r})\n"
            "def response(request, timeout):\n"
            "    url = request.full_url\n"
            "    if url.endswith('/SHA256SUMS'):\n"
            "        if os.environ.get('NO_CHECKSUMS'): raise urllib.error.HTTPError(url, 404, 'missing', {}, None)\n"
            "        files = ('compose.yaml', 'compose.backup-common.yaml', 'compose.backup.yaml', 'compose.backup-local.yaml', '.env.example', 'manage', 'manage.py', 'build/tools/backup-key.py', 'build/tools/restore-home.sh')\n"
            "        content = ''.join(hashlib.sha256((root / f).read_bytes()).hexdigest() + '  ' + f + '\\n' for f in files).encode()\n"
            "        if os.environ.get('BAD_CHECKSUM'): content = content.replace(content[:64], b'0' * 64)\n"
            "        return io.BytesIO(content)\n"
            "    if url.endswith('/releases/latest'): return io.BytesIO(b'{\"tag_name\":\"v4.141.0-1\",\"assets\":[{\"name\":\"SHA256SUMS\"}]}')\n"
            "    return io.BytesIO((root / url.split('/refs/tags/v4.141.0-1/', 1)[1]).read_bytes())\n"
            "urllib.request.urlopen = response\n"
            "if (tools / 'uname').exists(): platform.system = lambda: subprocess.check_output([str(tools / 'uname')], text=True).strip()\n"
            "if (tools / 'id').exists(): os.geteuid = lambda: int(subprocess.check_output([str(tools / 'id')], text=True).strip())\n"
        )
        # Installation tests must never invoke a real package manager or daemon.
        for name, body in {
            "docker": "exit 0",
            "gpg": "exit 0",
            "gh": "exit 0",
            "dpkg-query": "printf 'install ok installed\\n'",
            "brew": "exit 99",
            "apt-get": "exit 99",
            "sudo": "exit 99",
        }.items():
            tool = tools / name
            tool.write_text("#!/bin/sh\n" + body + "\n")
            tool.chmod(0o755)
        deployment = self.directory / "installed"
        command = ["sh", str(ROOT / "build/tools/install.sh"), "--version", "v4.141.0-1",
                   "--directory", str(deployment)]
        env = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "PYTHONPATH": str(tools)}
        return command, env, deployment

    def test_older_release_without_checksums_is_rejected(self):
        command, env, deployment = self.installer_fixture()
        result = subprocess.run(command, env={**env, "NO_CHECKSUMS": "1"}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lacks required deployment files or SHA256SUMS", result.stderr)
        self.assertFalse(deployment.exists())

    def test_installer_rejects_tampered_downloads(self):
        command, env, deployment = self.installer_fixture()
        result = subprocess.run(command, env={**env, "BAD_CHECKSUM": "1"}, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Release checksum missing or incorrect", result.stderr)
        self.assertFalse(deployment.exists())

    def test_installer_replaces_backup_symlink_without_following_it(self):
        command, env, deployment = self.installer_fixture()
        deployment.mkdir()
        victim = self.directory / 'victim'
        victim.write_text('preserve-me')
        (deployment / 'manage').write_text('old-launcher')
        (deployment / 'manage.bak').symlink_to(victim)
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(victim.read_text(), 'preserve-me')
        self.assertFalse((deployment / 'manage.bak').is_symlink())
        self.assertEqual((deployment / 'manage.bak').read_text(), 'old-launcher')

    def test_root_installer_rejects_untrusted_directory(self):
        command, env, deployment = self.installer_fixture()
        deployment.mkdir(mode=0o777)
        deployment.chmod(0o777)
        mock_id = self.directory / 'bin/id'
        mock_id.write_text('#!/bin/sh\necho 0\n')
        mock_id.chmod(0o755)
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Root installation requires root-owned directories', result.stderr)
        self.assertFalse((deployment / '.env').exists())

    @unittest.skipUnless(os.geteuid() == 0, 'requires root to test ancestor permissions')
    def test_root_installer_rejects_writable_ancestor(self):
        command, env, deployment = self.installer_fixture()
        parent = self.directory / 'writable-parent'
        parent.mkdir()
        parent.chmod(0o777)
        deployment = parent / 'installed'
        deployment.mkdir(mode=0o700)
        command[-1] = str(deployment)
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Root installation requires root-owned directories', result.stderr)
        self.assertIn(': ' + str(parent) + '\n', result.stderr)
        self.assertFalse((deployment / '.env').exists())

    def test_installer_updates_existing_image(self):
        command, env, deployment = self.installer_fixture()
        deployment.mkdir()
        dotenv = deployment / ".env"
        dotenv.write_text("CODE_SERVER_IMAGE=ghcr.io/weeks-consulting/code-server-aio:0.0.1\nBACKUP_GPG_PASSPHRASE=keep-secret\n")
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CODE_SERVER_IMAGE=ghcr.io/weeks-consulting/code-server-aio:4.141.0-1", dotenv.read_text())
        self.assertNotIn("keep-secret", result.stdout + result.stderr)
        self.assertNotIn(":0.0.1", dotenv.read_text())

    def test_installer_preserves_digest_pins(self):
        command, env, deployment = self.installer_fixture()
        deployment.mkdir()
        dotenv = deployment / ".env"
        digest = "ghcr.io/weeks-consulting/code-server-aio:4.141.0@sha256:" + "a" * 64
        for assignment in (f"CODE_SERVER_IMAGE={digest}\n",
                           f'export CODE_SERVER_IMAGE="{digest}" # verified\n'):
            for explicit_version in (False, True):
                with self.subTest(assignment=assignment, explicit_version=explicit_version):
                    original = assignment + "BACKUP_GPG_PASSPHRASE=keep-secret\n"
                    dotenv.write_text(original)
                    args = command if explicit_version else command[:2] + command[4:]
                    result = subprocess.run(args, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(dotenv.read_text().startswith(original))
                    self.assertIn("keeping existing CODE_SERVER_IMAGE digest pin", result.stderr)
                    self.assertNotIn("keep-secret", result.stdout + result.stderr)
                    self.assertEqual(dotenv.stat().st_mode & 0o777, 0o600)

    def test_installer_defaults_to_latest_image_on_install_and_refresh(self):
        command, env, deployment = self.installer_fixture()
        del command[2:4]  # Omit --version, retaining latest release file resolution.
        for initial in (None, "CODE_SERVER_IMAGE=example/custom:fixed\n"):
            with self.subTest(initial=initial):
                if initial is not None:
                    (deployment / ".env").write_text(initial)
                result = subprocess.run(command, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("CODE_SERVER_IMAGE=ghcr.io/weeks-consulting/code-server-aio:latest",
                              (deployment / ".env").read_text())

    def test_installer_dry_run_leaves_files_and_secrets_untouched(self):
        command, env, deployment = self.installer_fixture()
        result = subprocess.run(command + ["--dry-run"], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would create", result.stdout)
        self.assertFalse(deployment.exists())
        deployment.mkdir()
        dotenv = deployment / ".env"
        dotenv.write_text("BACKUP_GPG_PASSPHRASE=private-test-key\n")
        dotenv.chmod(0o644)
        compose = deployment / "compose.yaml"
        compose.write_text("original")
        # The curl stub cannot resolve latest: success proves dry-run made no request.
        result = subprocess.run([command[0], command[1], "--directory", str(deployment), "--dry-run"],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Would resolve the latest stable release", result.stdout)
        self.assertIn("compose.yaml.bak", result.stdout)
        self.assertNotIn("private-test-key", result.stdout + result.stderr)
        self.assertEqual(dotenv.read_text(), "BACKUP_GPG_PASSPHRASE=private-test-key\n")
        self.assertEqual(dotenv.stat().st_mode & 0o777, 0o644)
        self.assertEqual(compose.read_text(), "original")
        self.assertEqual(sorted(p.name for p in deployment.iterdir()), [".env", "compose.yaml"])

    def test_installer_installs_missing_compose(self):
        command, env, deployment = self.installer_fixture()
        tools = self.directory / "bin"
        marker = self.directory / "compose-installed"
        log = self.directory / "packages"
        for name, body in {
            "docker": f'test -f "{marker}"',
            "id": "echo 1000",
            "sudo": 'exec "$@"',
        }.items():
            (tools / name).write_text("#!/bin/sh\n" + body + "\n")
            (tools / name).chmod(0o755)
        # Exercise Homebrew everywhere and Debian apt on Linux.
        systems = ["Darwin"] + (["Linux"] if Path("/etc/debian_version").exists() else [])
        for system in systems:
            for ce in ([False, True] if system == "Linux" else [False]):
                with self.subTest(system=system, docker_ce=ce):
                    marker.unlink(missing_ok=True)
                    log.unlink(missing_ok=True)
                    (tools / "uname").write_text(f"#!/bin/sh\necho {system}\n")
                    (tools / "uname").chmod(0o755)
                    (tools / "dpkg-query").write_text(
                        "#!/bin/sh\ncase \"$*\" in *docker-ce-cli*) " +
                        ("printf 'install ok installed\\n'" if ce else "exit 1") +
                        ";; *) printf 'install ok installed\\n';; esac\n")
                    for manager in ("brew", "apt-get"):
                        (tools / manager).write_text(
                            f"#!{sys.executable}\nimport pathlib, sys\n"
                            f"with pathlib.Path({str(log)!r}).open('a') as output: output.write(' '.join(sys.argv[1:]) + '\\n')\n"
                            f"if sys.argv[1] == 'install': pathlib.Path({str(marker)!r}).touch()\n")
                    preview = subprocess.run(command + ["--dry-run"], env=env, capture_output=True, text=True)
                    self.assertEqual(preview.returncode, 0, preview.stderr)
                    package = "docker-compose-plugin" if ce else "docker-compose"
                    self.assertIn(package, preview.stdout)
                    self.assertFalse(marker.exists())
                    self.assertFalse(log.exists())
                    result = subprocess.run(command, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(package, log.read_text())
                    self.assertTrue((deployment / ".env").exists())

    def test_installer_requires_python_before_dependency_setup(self):
        command, env, deployment = self.installer_fixture()
        tools = self.directory / "bin"
        (tools / "dirname").symlink_to(shutil.which("dirname"))
        env["PATH"] = str(tools)
        result = subprocess.run(["/bin/sh", *command[1:], "--dry-run"],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("python3", result.stderr)
        self.assertFalse(deployment.exists())

    def test_installer_generates_and_preserves_key_on_refresh(self):
        command, env, deployment = self.installer_fixture()
        for initial in (None, "BACKUP_GPG_PASSPHRASE=\n", "BACKUP_GPG_PASSPHRASE='custom secret'\n"):
            with self.subTest(initial=initial):
                deployment.mkdir(exist_ok=True)
                dotenv = deployment / ".env"
                if initial is None:
                    dotenv.unlink(missing_ok=True)
                else:
                    dotenv.write_text(initial)
                result = subprocess.run(command, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(os.access(deployment / "manage", os.X_OK))
                contents = dotenv.read_text()
                if initial is None:
                    self.assertIn("CODE_SERVER_IMAGE=ghcr.io/weeks-consulting/code-server-aio:4.141.0-1", contents)
                if initial and "custom secret" in initial:
                    self.assertIn(initial, contents)
                else:
                    self.assertRegex(contents, r"BACKUP_GPG_PASSPHRASE=[a-f0-9]{64}")
                self.assertEqual(dotenv.stat().st_mode & 0o777, 0o600)
                result = subprocess.run(command, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(dotenv.read_text(), contents)

    def test_installer_merges_new_settings_and_preserves_existing_lines(self):
        command, env, deployment = self.installer_fixture()
        deployment.mkdir()
        dotenv = deployment / ".env"
        marker = self.directory / "must-not-exist"
        original = ("# My existing configuration\n"
                    "CODE_SERVER_IMAGE=example/custom:fixed\n"
                    "export CF_ACCESS_AUD='custom audience'\n"
                    "export BACKUP_ID = \n"
                    "BACKUP_GPG_PASSPHRASE='already-set'\n"
                    f"CUSTOM=$(touch {marker})\n")
        dotenv.write_text(original)
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        contents = dotenv.read_text()
        self.assertTrue(contents.startswith(original.replace("example/custom:fixed", "ghcr.io/weeks-consulting/code-server-aio:4.141.0-1")))
        self.assertIn("# Settings added by installer for release v4.141.0-1", contents)
        for key in ("CODE_SERVER_IMAGE", "CF_ACCESS_AUD", "BACKUP_ID", "BACKUP_GPG_PASSPHRASE"):
            self.assertEqual(len(re.findall(rf"^(?:export )?{key}\s*=", contents, re.M)), 1)
        self.assertFalse(marker.exists())
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(dotenv.read_text(), contents)

    def test_installer_rejects_missing_and_option_values(self):
        for option in ("--directory", "--version", "--repository"):
            for value in (None, "", "--version"):
                with self.subTest(option=option, value=value):
                    args = ["sh", str(ROOT / "build/tools/install.sh"), option]
                    if value is not None:
                        args.append(value)
                    result = subprocess.run(args, cwd=self.directory, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(option, result.stderr)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_rejects_symlinks(self):
        target = self.directory / "target"
        target.write_text("unchanged")
        self.env.symlink_to(target)
        self.assertNotEqual(self.initialize().returncode, 0)
        self.assertEqual(target.read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
