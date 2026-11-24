"""Run the production Compose topology with real code-server and disposable Access keys.

The fixture and client have no backend socket mount. Only the JWKS transport is replaced; production socket permissions and systemd lifecycle apply.
"""

import base64
import http.client
import http.server
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import tempfile
import time

DOMAIN = 'test.cloudflareaccess.com'
AUD = 'a' * 64


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def fixture():
    key = '/tmp/access-key.pem'
    subprocess.run(['openssl', 'genrsa', '-out', key, '2048'], check=True, capture_output=True)
    modulus = subprocess.check_output(['openssl', 'rsa', '-in', key, '-modulus', '-noout'], stderr=subprocess.DEVNULL)
    jwks = json.dumps({'keys': [{'kty': 'RSA', 'use': 'sig', 'alg': 'RS256', 'kid': 'test',
                               'n': b64(bytes.fromhex(modulus.decode().strip().split('=')[1])), 'e': 'AQAB'}]}).encode()
    message = '.'.join(b64(json.dumps(value).encode()) for value in (
        {'alg': 'RS256', 'kid': 'test'}, {'iss': 'https://' + DOMAIN, 'aud': [AUD],
                                         'email': 'user@example.com', 'exp': int(time.time()) + 3600}))
    signature = subprocess.check_output(['openssl', 'dgst', '-sha256', '-sign', key], input=message.encode())
    token = (message + '.' + b64(signature)).encode()
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = token if self.path == '/token' else jwks
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    http.server.ThreadingHTTPServer(('0.0.0.0', 8088), Handler).serve_forever()


def request(host, port, path='/', headers=None):
    connection = http.client.HTTPConnection(host, port, timeout=3)
    try:
        connection.request('GET', path, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read().decode()
    finally:
        connection.close()


def blocked(host, port):
    try:
        connection = socket.create_connection((host, port), timeout=1)
    except OSError:
        return
    connection.close()
    raise AssertionError(f'Unexpected TCP endpoint reachable: {host}:{port}')


def probe(expected, backend_ip):
    for path in ('/run/code-server/backend.sock', '/run/envoy/admin.sock', '/run/envoy/health.sock'):
        assert not Path(path).exists(), f'Client has access to private socket {path}'
    # The old origin ports, backend, and admin must all remain unreachable over TCP.
    for port in (8080, 18080, 8081, 9901):
        blocked('code-server', port)
        blocked(backend_ip, port)
    curl = ['curl', '--silent', '--max-time', '3', '--unix-socket', '/var/lib/tunnel-origin/envoy.sock']
    if expected == 'down':
        result = subprocess.run([*curl, 'http://localhost/'], capture_output=True)
        assert result.returncode == 7, result
        return
    status = subprocess.check_output([*curl, '--output', '/dev/null', '--write-out', '%{http_code}',
                                      'http://localhost/'], text=True)
    assert status == '403', status
    _, token = request('fixture', 8088, '/token')
    response = subprocess.check_output([*curl, '--header', 'Cf-Access-Jwt-Assertion: ' + token,
                                        '--write-out', '\n%{http_code}', 'http://localhost/healthz'], text=True)
    body, status = response.rsplit('\n', 1)
    assert status == expected, (status, body)
    if expected == '200':
        assert json.loads(body)['status'] in ('alive', 'expired'), body
    elif expected == '503':
        assert 'Service is not ready' in body, body
    else:
        assert 'Cloudflare Access authentication required' in body, body


def integration(image):
    root = Path(__file__).resolve().parents[2]
    project = f'envoy-isolation-{os.getpid()}'
    # Under the checkout so Docker Desktop and act can mount the test files.
    with tempfile.TemporaryDirectory(prefix='.test-tmp.', dir=os.environ.get('TEST_TMPDIR', root)) as directory:
        directory = Path(directory).resolve()
        overlay = directory / 'compose.json'
        mount = {'type': 'bind', 'source': str(Path(__file__).resolve()), 'target': '/tmp/isolation.py', 'read_only': True}
        override = {'services': {
            'code-server': {'image': image, 'environment': {'CF_ACCESS_TEAM_DOMAIN': DOMAIN, 'CF_ACCESS_AUD': AUD,
                                     'CF_ACCESS_ALLOWED_EMAILS': 'user@example.com'},
                      'healthcheck': {'interval': '1s', 'start_period': '10s', 'retries': 30}},
            'fixture': {'image': image, 'entrypoint': ['/usr/bin/python3', '/tmp/isolation.py', '--fixture'],
                        'init': True, 'stop_signal': 'SIGTERM',
                        'volumes': [mount], 'healthcheck': {
                            'test': ['CMD', 'curl', '-fsS', 'http://localhost:8088/token'],
                            'interval': '1s', 'timeout': '2s', 'retries': 20}},
            'client': {'image': image, 'entrypoint': ['/usr/bin/python3', '/tmp/isolation.py', '--probe'],
                       'user': '65532:65532', 'group_add': ['10001'],
                       'volumes': [mount, 'tunnel-origin:/var/lib/tunnel-origin:ro']},
        }}
        compose = ['docker', 'compose', '--project-directory', str(root), '--project-name', project,
                   '--env-file', '/dev/null', '-f', str(root / 'compose.yaml'),
                   '-f', str(overlay)]
        env = {**os.environ, 'CF_TUNNEL_TOKEN': 'test-only',
               'CF_ACCESS_TEAM_DOMAIN': DOMAIN, 'CF_ACCESS_AUD': AUD}
        import importlib.util
        spec = importlib.util.spec_from_file_location('launcher', root / 'build/runtime/start-envoy.py')
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        config = launcher.render((root / 'build/runtime/envoy.json.template').read_text(), env)
        # Replace only the JWKS transport with the disposable local fixture.
        cluster = next(item for item in config['static_resources']['clusters']
                       if item['name'] == 'cloudflare_jwks')
        del cluster['transport_socket']
        address = cluster['load_assignment']['endpoints'][0]['lb_endpoints'][0]['endpoint']['address']['socket_address']
        address.update(address='fixture', port_value=8088)
        manager = config['static_resources']['listeners'][0]['filter_chains'][0]['filters'][0]['typed_config']
        jwt = next(item['typed_config'] for item in manager['http_filters']
                   if item['name'] == 'envoy.filters.http.jwt_authn')
        jwt['providers']['cloudflare']['remote_jwks']['http_uri']['uri'] = 'http://fixture:8088/certs'
        template = directory / 'envoy.json.template'
        template.write_text(json.dumps(config))
        override['services']['code-server']['volumes'] = [{'type': 'bind', 'source': str(template),
            'target': '/etc/envoy/envoy.json.template', 'read_only': True}]
        overlay.write_text(json.dumps(override))
        def run(*args, **kwargs):
            return subprocess.check_output([*compose, *args], env=env, text=True, **kwargs)
        try:
            run('up', '-d', '--wait', '--wait-timeout', '120', 'code-server', 'fixture')
            app = run('ps', '-q', 'code-server').strip()
            info = json.loads(subprocess.check_output(['docker', 'inspect', app], text=True))[0]
            backend_ip = info['NetworkSettings']['Networks'][project + '_default']['IPAddress']
            run('run', '--rm', '--no-deps', 'client', '200', backend_ip)
            # Access environment changes recreate the combined container.
            for email, expected in (('denied@example.com', '403'), ('user@example.com', '200')):
                override['services']['code-server']['environment']['CF_ACCESS_ALLOWED_EMAILS'] = email
                overlay.write_text(json.dumps(override))
                run('up', '-d', '--wait', '--wait-timeout', '120', 'code-server')
                run('run', '--rm', '--no-deps', 'client', expected, backend_ip)
            # Fresh configuration and empty in-memory key cache work on container restart.
            run('restart', 'code-server')
            run('up', '-d', '--wait', '--wait-timeout', '120', 'code-server')
            time.sleep(2)
            run('run', '--rm', '--no-deps', 'client', '200', backend_ip)
            run('exec', '-T', 'code-server', 'systemctl', 'stop', 'code-server')
            run('run', '--rm', '--no-deps', 'client', '503', backend_ip)
            # Type=exec reports a successful start before the backend opens its socket.
            run('exec', '-T', 'code-server', '/bin/sh', '-ec',
                'mkdir -p /run/systemd/system/code-server.service.d; '
                'cat > /run/systemd/system/code-server.service.d/delayed-start.conf; systemctl daemon-reload',
                input="[Service]\nExecStart=\n"
                      "ExecStart=/bin/sh -ec 'sleep 3; exec /bin/sh /usr/local/lib/start-code-server.sh'\n")
            run('exec', '-T', 'code-server', 'systemctl', 'start', 'code-server')
            # Docker may still report healthy from before the in-place service restart.
            run('exec', '-T', 'code-server', 'timeout', '30', '/bin/sh', '-c',
                'until /usr/local/bin/container-healthcheck >/dev/null 2>&1; do sleep 1; done')
            run('run', '--rm', '--no-deps', 'client', '200', backend_ip)
            run('exec', '-T', 'code-server', 'systemctl', 'stop', 'envoy')
            run('run', '--rm', '--no-deps', 'client', 'down', backend_ip)
            print('PASS: Unix-socket backend, supervised Envoy, real code-server, restarts, and fail-closed routing.')
        except BaseException:
            print(run('logs', '--no-color'), file=sys.stderr)
            raise
        finally:
            run('down', '--volumes', '--remove-orphans')


if __name__ == '__main__':
    if sys.argv[1] == '--fixture':
        fixture()
    elif sys.argv[1] == '--probe':
        probe(*sys.argv[2:])
    else:
        def interrupted(signum, frame):
            raise SystemExit(128 + signum)
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGHUP, interrupted)
        integration(sys.argv[1])
