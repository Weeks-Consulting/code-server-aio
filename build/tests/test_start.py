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

    def test_initialization_starts_service_and_shuts_down_on_failure(self):
        script = (ROOT / 'build/runtime/initialize-workspace.sh').read_text()
        script = script.replace('/usr/bin/supervisorctl', 'fake_supervisorctl')
        functions = '''mkdir() { printf 'mkdir %s\\n' "$*"; }
chown() { printf 'chown %s\\n' "$*"; }
chmod() { printf 'chmod %s\\n' "$*"; }
getent() { echo 'cloud-user:x:123:456::/home/cloud-user:/bin/bash'; }
fake_supervisorctl() {
    printf 'supervisorctl %s\\n' "$*"
    if [[ "$*" == 'start code-server' ]]; then return "$START_RESULT"; fi
}
'''
        for failure in ('none', 'start', 'mkdir', 'getent'):
            with self.subTest(failure=failure):
                setup = functions + ('START_RESULT=7\n' if failure == 'start' else 'START_RESULT=0\n')
                if failure in ('mkdir', 'getent'):
                    setup += failure + '() { return 9; }\n'
                result = subprocess.run(['bash', '-c', setup + script], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0 if failure == 'none' else 1, result.stderr)
                if failure == 'none':
                    self.assertEqual(result.stdout.splitlines(), [
                        'mkdir -p /run/code-server', 'chown 10000:10000 /run/code-server',
                        'chmod 0750 /run/code-server', 'mkdir -p /home/cloud-user',
                        'chown 123:456 /home/cloud-user', 'supervisorctl start code-server'])
                else:
                    self.assertIn('supervisorctl shutdown', result.stdout)
                    self.assertIn('Workspace initialization failed', result.stderr)
                    if failure != 'start':
                        self.assertNotIn('supervisorctl start code-server', result.stdout)

    def test_envoy_renderer_escapes_values_and_preserves_lua(self):
        import json
        renderer = load('start-envoy')
        template = (ROOT / 'build/runtime/envoy.json.template').read_text()
        raw = json.loads(template)
        domain = 'invalid"\\host\n'
        rendered = renderer.render(template, {'CF_ACCESS_TEAM_DOMAIN': domain, 'CF_ACCESS_AUD': 'a' * 64})
        rendered = json.loads(json.dumps(rendered))
        manager = lambda config: config['static_resources']['listeners'][0]['filter_chains'][0]['filters'][0]['typed_config']
        self.assertEqual(manager(rendered)['http_filters'][0], manager(raw)['http_filters'][0])
        provider = manager(rendered)['http_filters'][2]['typed_config']['providers']['cloudflare']
        self.assertEqual(provider['issuer'], 'https://' + domain)
        self.assertEqual(provider['audiences'], ['a' * 64])
        self.assertEqual(renderer.render(template, {})['static_resources']['clusters'][1]
                         ['transport_socket']['typed_config']['sni'], '')

    def test_healthcheck_requires_running_services_and_completed_initializer(self):
        script = (ROOT / 'build/runtime/container-healthcheck.sh').read_text()
        script = script.replace('timeout 3 /usr/bin/supervisorctl', 'fake_supervisorctl').replace(
            'timeout 3 /usr/bin/docker', 'fake_docker').replace('code=$(curl ', 'code=$(fake_curl ')
        functions = '''fake_supervisorctl() {
    test "$*" = "status service-events dockerd code-server envoy initialize-workspace" || return 1
    printf '%s\\n' 'service-events RUNNING' 'dockerd RUNNING' 'code-server RUNNING' 'envoy RUNNING' 'initialize-workspace EXITED'
    return 3
}
fake_docker() { echo 1; }
fake_curl() { case "$*" in *"--unix-socket /run/code-server/backend.sock"*|*"127.0.0.1:9901/ready"*) echo 200 ;; *"127.0.0.1:8080/"*) echo 403 ;; *) return 1 ;; esac; }
'''
        result = subprocess.run(['bash', '-c', functions + script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for transition in ('code-server STARTING', 'envoy FATAL', 'initialize-workspace RUNNING'):
            before = 'code-server RUNNING' if transition.startswith('code-server') else 'envoy RUNNING' if transition.startswith('envoy') else 'initialize-workspace EXITED'
            result = subprocess.run(['bash', '-c', functions.replace(before, transition) + script],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
