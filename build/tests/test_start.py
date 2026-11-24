"""Exercise the proxy startup gate without starting workspace services."""

from pathlib import Path
import os
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

    def test_entrypoint_initializes_before_systemd_and_aborts_on_failure(self):
        script = (ROOT / 'build/runtime/entrypoint.sh').read_text()
        with tempfile.TemporaryDirectory() as directory:
            systemd = Path(directory) / 'systemd'
            systemd.write_text('#!/bin/sh\nprintf "systemd %s\\n" "$*"\n')
            systemd.chmod(0o755)
            script = script.replace('/usr/lib/systemd/systemd', str(systemd))
            functions = """grep() { test "$*" = '-qx 0::/ /proc/self/cgroup' && printf 'cgroup check\n'; }
mount() { printf 'mount %s\\n' "$*"; }
mkdir() { printf 'mkdir %s\\n' "$*"; }
chown() { printf 'chown %s\\n' "$*"; }
getent() { echo 'cloud-user:x:123:456::/home/cloud-user:/bin/bash'; }
"""
            for failure in ('none', 'grep', 'mount', 'mkdir', 'chown', 'getent'):
                with self.subTest(failure=failure):
                    setup = functions
                    if failure != 'none':
                        setup += failure + '() { return 9; }\n'
                    result = subprocess.run(['bash', '-c', setup + script, 'entrypoint', '--test'],
                                            capture_output=True, text=True)
                    expected = 0 if failure == 'none' else 1 if failure in ('grep', 'mount') else 9
                    self.assertEqual(result.returncode, expected, result.stderr)
                    if failure == 'none':
                        self.assertEqual(result.stdout.splitlines(), [
                            'cgroup check', 'mount -o remount,bind,rw /sys/fs/cgroup',
                            'mkdir -p /home/cloud-user',
                            'chown 123:456 /home/cloud-user',
                            'systemd --test'])
                    else:
                        self.assertNotIn('systemd', result.stdout)
                    if failure == 'grep':
                        self.assertIn('requires cgroup v2', result.stderr)
                        self.assertNotIn('mount', result.stdout)
                    if failure == 'mount':
                        self.assertIn('could not make /sys/fs/cgroup writable', result.stderr)
                        self.assertNotIn('mkdir', result.stdout)

    def test_envoy_renderer_escapes_values_and_preserves_lua(self):
        import json
        renderer = load('start-envoy')
        template = (ROOT / 'build/runtime/envoy.json.template').read_text()
        raw = json.loads(template)
        domain = 'invalid"\\host\n'
        rendered = renderer.render(template, {'CF_ACCESS_TEAM_DOMAIN': domain, 'CF_ACCESS_AUD': 'a' * 64})
        rendered = json.loads(json.dumps(rendered))
        manager = lambda config: config['static_resources']['listeners'][0]['filter_chains'][0]['filters'][0]['typed_config']
        lua = lambda config: [item for item in manager(config)['http_filters']
                              if item['name'] == 'envoy.filters.http.lua']
        self.assertEqual(lua(rendered), lua(raw))
        provider = next(item for item in manager(rendered)['http_filters']
                        if item['name'] == 'envoy.filters.http.jwt_authn')['typed_config']['providers']['cloudflare']
        self.assertEqual(provider['issuer'], 'https://' + domain)
        self.assertEqual(provider['audiences'], ['a' * 64])
        jwks = next(item for item in renderer.render(template, {})['static_resources']['clusters']
                    if item['name'] == 'cloudflare_jwks')
        self.assertEqual(jwks['transport_socket']['typed_config']['sni'], '')
        self.assertEqual(rendered['admin']['address'],
                         {'pipe': {'path': '/run/envoy/admin.sock', 'mode': 0o600}})
        mappers = manager(rendered)['local_reply_config']['mappers']
        self.assertEqual([item['status_code_filter']['comparison']['value']['default_value']
                          for item in mappers[0]['filter']['or_filter']['filters']], [401, 403])
        self.assertEqual(mappers[0]['status_code'], 403)
        self.assertEqual(mappers[1]['status_code'], 503)
        sources = [item['typed_config']['default_source_code']
                   for item in manager(rendered)['http_filters']
                   if item['name'] == 'envoy.filters.http.lua']
        filters = manager(rendered)['http_filters']
        names = [item['name'] for item in filters]
        self.assertEqual(names[-3:], ['envoy.filters.http.lua', 'envoy.filters.http.lua',
                                      'envoy.filters.http.router'])
        self.assertEqual([source['filename'] for source in sources[-2:]],
                         ['/etc/envoy/lua/identity.lua', '/etc/envoy/lua/update-notice.lua'])
        self.assertLess(names.index('envoy.filters.http.jwt_authn'), len(names) - 3)
        for source in sources + [item['body'] for item in mappers]:
            asset = ROOT / 'build/runtime/envoy' / Path(source['filename']).relative_to('/etc/envoy')
            self.assertTrue(asset.is_file(), asset)
        self.assertEqual(renderer.render(json.dumps(rendered),
                                        {'CF_ACCESS_TEAM_DOMAIN': domain, 'CF_ACCESS_AUD': 'a' * 64}), rendered)

    def test_healthcheck_requires_running_services(self):
        script = (ROOT / 'build/runtime/container-healthcheck.sh').read_text()
        script = script.replace('timeout 3 /usr/bin/env', 'fake_env').replace('code=$(curl ', 'code=$(fake_curl ')
        script = script.replace('timeout 2 /usr/bin/env', 'fake_env')
        script = script.replace('[[ -S /var/lib/tunnel-origin/envoy.sock ]]', 'true')
        functions = '''fake_user_bus() {
    test "$*" = "--reuid=cloud-user --regid=cloud-user --clear-groups /usr/bin/busctl --address=unix:path=/run/user/10000/bus call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus GetId"
}
fake_systemctl() {
    test "$*" = "--system is-active dockerd.service code-server.service envoy.service user@10000.service" || return 1
    printf '%s\\n' active active active active
}
fake_docker() { echo server-version=26.1.5; }
fake_env() {
    if [ "$1 $2" = '-i /usr/bin/systemctl' ]; then
        shift 2
        fake_systemctl "$@"
        return
    fi
    if [ "$1 $2" = '-i /usr/bin/setpriv' ]; then
        shift 2
        fake_user_bus "$@"
        return
    fi
    test "$*" = '-i /usr/bin/docker --config /run --host unix:///var/run/docker.sock info --format server-version={{.ServerVersion}}' || return 1
    fake_docker
}
fake_curl() {
    test "$1" = --disable || return 1
    case "$*" in *"--unix-socket /run/code-server/backend.sock"*|*"--unix-socket /run/envoy/admin.sock"*) echo 200 ;; *"--unix-socket /run/envoy/health.sock"*) echo 403 ;; *) return 1 ;; esac;
}
'''
        result = subprocess.run(['bash', '-c', functions + script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        online = functions.replace('*) return 1 ;; esac;',
                                   '*"https://test.cloudflareaccess.com/cdn-cgi/access/certs"*) echo 200 ;; *) return 1 ;; esac;')
        result = subprocess.run(['bash', '-c', online + script, 'healthcheck', '--access'],
                                env={**os.environ, 'CF_ACCESS_TEAM_DOMAIN': 'test.cloudflareaccess.com'},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run(['bash', '-c', functions + script, 'healthcheck', '--access'],
                                env={**os.environ, 'CF_ACCESS_TEAM_DOMAIN': 'test.cloudflareaccess.com'},
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        for unexpected in ('200', '503'):
            result = subprocess.run(['bash', '-c', functions.replace('echo 403', 'echo ' + unexpected) + script],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('expected 403', result.stderr)
        # Docker CLI 26 exits 0 with an empty version when it cannot connect.
        for docker, message in (
                ('fake_docker() { echo server-version=; echo "Cannot connect to the Docker daemon" >&2; }',
                 'Cannot connect to the Docker daemon'),
                ('fake_docker() { return 124; }', 'did not respond within 3 seconds'),
                ('fake_docker() { echo server-version=26.1.5; return 1; }', 'Docker is not ready')):
            with self.subTest(docker=docker):
                result = subprocess.run(['bash', '-c', functions.replace(
                    'fake_docker() { echo server-version=26.1.5; }', docker) + script],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
        for states in ('activating active active active', 'active failed active active',
                       'active active inactive active', 'active active active activating', 'active'):
            with self.subTest(states=states):
                result = subprocess.run(['bash', '-c', functions.replace('active active active active', states)
                                         + script], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('services not ready', result.stderr)
        for failure in ('return 1', 'return 124'):
            with self.subTest(bus_failure=failure):
                result = subprocess.run(['bash', '-c', functions.replace(
                    'fake_user_bus() {', 'fake_user_bus() { ' + failure + ';') + script],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Workspace user bus is not ready', result.stderr)

if __name__ == "__main__":
    unittest.main()
