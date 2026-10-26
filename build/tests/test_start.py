"""Exercise the proxy startup gate without starting workspace services."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, ROOT / 'build/runtime' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StartupTests(unittest.TestCase):
    def test_code_server_uses_only_a_unix_socket_and_removes_stale_socket(self):
        script = (ROOT / 'build/runtime/start-code-server.sh').read_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stale = root / 'backend.sock'
            stale.write_text('stale socket')
            binary = root / 'code-server'
            binary.write_text('#!/bin/sh\ntest ! -e "' + str(stale) + '" || exit 1\nprintf "%s\\n" "$@"\n')
            binary.chmod(0o755)
            candidate = script.replace('/run/code-server/backend.sock', str(stale)).replace(
                '/usr/bin/code-server', str(binary))
            result = subprocess.run(['sh', '-c', candidate], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ['--socket', str(stale), '--socket-mode',
                                                         '660', '--auth', 'none', '/home/cloud-user'])

    def test_initialization_starts_service_only_after_preinstall_success(self):
        from unittest.mock import patch, Mock
        import os
        module = load('initialize-workspace')
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            user = Mock(pw_dir=str(home), pw_uid=123, pw_gid=456)
            for failing in (False, True):
                with self.subTest(failing=failing), patch.object(module, 'Path') as paths, \
                        patch.object(module.pwd, 'getpwnam', return_value=user), \
                        patch.object(module.os, 'chown') as chown, \
                        patch.dict(os.environ), patch.object(module.subprocess, 'run') as run:
                    if failing:
                        run.side_effect = [subprocess.CalledProcessError(124, 'preinstall'), None]
                    self.assertEqual(module.main(), int(failing))
                    calls = [call.args[0] for call in run.call_args_list]
                    self.assertEqual(calls[0], ['/bin/bash', '/usr/local/lib/preinstall.sh'])
                    self.assertEqual(calls[-1], [module.SUPERVISORCTL, 'shutdown'] if failing else
                                     [module.SUPERVISORCTL, 'start', 'code-server'])
                    self.assertEqual(os.environ['PATH'], module.SYSTEM_PATH)
                    self.assertEqual(chown.call_args_list[-1].args, (paths.return_value, 123, 456))
                    self.assertEqual(chown.call_args_list[0].args, (paths.return_value, 10000, 10000))
                    paths.return_value.chmod.assert_called_once_with(0o750)
                    self.assertTrue(paths.return_value.mkdir.called)

    def test_healthcheck_requires_running_services_and_completed_initializer(self):
        script = (ROOT / 'build/runtime/container-healthcheck.sh').read_text()
        script = script.replace('timeout 3 /usr/bin/supervisorctl', 'fake_supervisorctl').replace(
            'timeout 3 /usr/bin/docker', 'fake_docker').replace('code=$(curl ', 'code=$(fake_curl ')
        functions = '''fake_supervisorctl() {
    test "$*" = "status service-events dockerd code-server initialize-workspace" || return 1
    printf '%s\\n' 'service-events RUNNING' 'dockerd RUNNING' 'code-server RUNNING' 'initialize-workspace EXITED'
    return 3
}
fake_docker() { echo 1; }
fake_curl() { case "$*" in *"--unix-socket /run/code-server/backend.sock"*) echo 200 ;; *) return 1 ;; esac; }
'''
        result = subprocess.run(['bash', '-c', functions + script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for transition in ('code-server STARTING', 'initialize-workspace RUNNING'):
            before = 'code-server RUNNING' if transition.startswith('code-server') else 'initialize-workspace EXITED'
            result = subprocess.run(['bash', '-c', functions.replace(before, transition) + script],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
