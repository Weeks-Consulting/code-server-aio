"""Exercise the real Envoy binary with local HTTPS JWKS and a Unix-socket backend."""

import base64
import hashlib
import http.client
import http.server
import copy
import json
import os
from pathlib import Path
import socket
import socketserver
import ssl
import subprocess
import tempfile
import threading
import time
import unittest

import importlib.util

spec = importlib.util.spec_from_file_location('start_envoy', '/usr/local/lib/start-envoy.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)
BOOTSTRAP = launcher.render(Path('/etc/envoy/envoy.json.template').read_text(), {
    'CF_ACCESS_TEAM_DOMAIN': 'test.cloudflareaccess.com', 'CF_ACCESS_AUD': 'a' * 64})
AUD = 'a' * 64
DOMAIN = 'test.cloudflareaccess.com'
ISSUER = 'https://' + DOMAIN


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode()


def command(*args, **kwargs):
    return subprocess.check_output(args, stderr=subprocess.DEVNULL, **kwargs)


class Backend(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def do_GET(self):
        if self.headers.get('Upgrade', '').lower() == 'websocket':
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.send_header('Sec-WebSocket-Accept', base64.b64encode(hashlib.sha1(
                (self.headers['Sec-WebSocket-Key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()
            ).digest()).decode())
            self.end_headers()
            try:
                while block := self.rfile.read(4):
                    self.wfile.write(block)
                    self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass
        else:
            body = b'backend reached'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):
        pass


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


class Keys(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.server.fail:
            self.send_error(503)
            return
        document = json.dumps({'keys': self.server.keys}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(document)))
        self.end_headers()
        self.wfile.write(document)

    def log_message(self, *args):
        pass


class AccessProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.root.chmod(0o755)
        cls.private = []
        cls.jwks = []
        for index in range(2):
            path = cls.root / f'key-{index}.pem'
            command('openssl', 'genrsa', '-out', str(path), '2048')
            cls.private.append(path)
            modulus = command('openssl', 'rsa', '-in', str(path), '-modulus', '-noout').decode().strip().split('=')[1]
            cls.jwks.append({'kty': 'RSA', 'use': 'sig', 'alg': 'RS256', 'kid': str(index),
                             'n': b64(bytes.fromhex(modulus)), 'e': 'AQAB'})
        cls.cert = cls.root / 'cert.pem'
        tls_key = cls.root / 'tls.pem'
        command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                '-keyout', str(tls_key), '-out', str(cls.cert), '-subj', '/CN=' + DOMAIN,
                '-addext', 'subjectAltName=DNS:' + DOMAIN)
        cls.keys_server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Keys)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, tls_key)
        cls.keys_server.socket = context.wrap_socket(cls.keys_server.socket, server_side=True)
        threading.Thread(target=cls.keys_server.serve_forever, daemon=True).start()
        cls.socket_path = cls.root / 'backend.sock'
        cls.backend = UnixServer(str(cls.socket_path), Backend)
        cls.socket_path.chmod(0o666)
        threading.Thread(target=cls.backend.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.backend.shutdown()
        cls.backend.server_close()
        cls.keys_server.shutdown()
        cls.keys_server.server_close()
        cls.directory.cleanup()

    def setUp(self):
        self.process = None
        self.log = tempfile.TemporaryFile()
        self.keys_server.keys = [self.jwks[0]]
        self.keys_server.fail = False
        self.start()

    def tearDown(self):
        self.stop()
        self.log.close()

    def stop(self):
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=10)
            self.process = None

    def start(self, identities=(), change=None, env_override=None, expect_ready=True):
        self.stop()
        config = copy.deepcopy(BOOTSTRAP)
        # Fixture clients use loopback TCP; production uses a private Unix socket.
        config['admin']['address'] = {'socket_address': {'address': '127.0.0.1', 'port_value': 9901}}
        resources = config['static_resources']
        manager = resources['listeners'][0]['filter_chains'][0]['filters'][0]['typed_config']
        jwt = next(item['typed_config'] for item in manager['http_filters']
                   if item['name'] == 'envoy.filters.http.jwt_authn')
        provider = jwt['providers']['cloudflare']
        remote = provider['remote_jwks']
        remote['http_uri']['timeout'] = '0.2s'
        remote['cache_duration'] = '3s'
        remote['retry_policy'] = {'num_retries': 0}
        clusters = {item['name']: item for item in resources['clusters']}
        backend, jwks = clusters['code_server'], clusters['cloudflare_jwks']
        backend['load_assignment']['endpoints'][0]['lb_endpoints'][0]['endpoint']['address']['pipe']['path'] = str(self.socket_path)
        jwks['type'] = 'STATIC'
        address = jwks['load_assignment']['endpoints'][0]['lb_endpoints'][0]['endpoint']['address']['socket_address']
        address.update(address='127.0.0.1', port_value=self.keys_server.server_port)
        validation = jwks['transport_socket']['typed_config']['common_tls_context']['validation_context']
        validation['trusted_ca']['filename'] = str(self.cert)
        if change:
            change(config, manager, provider)
        path = self.root / 'envoy.json'
        path.write_text(json.dumps(config))
        def demote():
            os.setgroups([])
            os.setgid(10000)
            os.setuid(10000)
        environment = {**os.environ, 'CF_ACCESS_TEAM_DOMAIN': DOMAIN, 'CF_ACCESS_AUD': AUD,
                       'CF_ACCESS_ALLOWED_EMAILS': ','.join(value for claim, value in identities if claim == 'email'),
                       'CF_ACCESS_ALLOWED_SERVICE_TOKENS': ','.join(value for claim, value in identities if claim == 'common_name'),
                       **(env_override or {})}
        self.process = subprocess.Popen(['python3', '-c',
                                         'import importlib.util, os, sys; '
                                         's=importlib.util.spec_from_file_location("launcher", "/usr/local/lib/start-envoy.py"); '
                                         'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
                                         '\ntry: m.validate(os.environ)'
                                         '\nexcept ValueError as e: sys.exit(str(e))'
                                         '\nos.execv(sys.argv[1], sys.argv[1:])', '/usr/local/bin/envoy', '-c', str(path), '--concurrency', '2',
                                         '--disable-hot-restart', '--log-level', 'warn'],
                                        stdout=self.log, stderr=self.log, preexec_fn=demote, env=environment)
        if expect_ready:
            self.wait_status(None, 403)

    def token(self, changes=None, key=0, algorithm='RS256', omit=()):
        payload = {'iss': ISSUER, 'aud': [AUD], 'exp': int(time.time()) + 300}
        payload.update(changes or {})
        for claim in omit:
            payload.pop(claim, None)
        message = '.'.join(b64(json.dumps(part).encode()) for part in [
            {'alg': algorithm, 'kid': str(key), 'typ': 'JWT'}, payload])
        signature = command('openssl', 'dgst', '-sha256', '-sign', str(self.private[key]), input=message.encode())
        return message + '.' + b64(signature)

    @staticmethod
    def response(token=None, path='/', headers=None, method='GET', source='127.0.0.1'):
        connection = http.client.HTTPConnection('127.0.0.1', 8080, timeout=3,
                                                source_address=(source, 0))
        request_headers = dict(headers or {})
        if token is not None:
            request_headers['Cf-Access-Jwt-Assertion'] = token
        try:
            connection.request(method, path, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            connection.close()

    @classmethod
    def status(cls, *args, **kwargs):
        return cls.response(*args, **kwargs)[0]

    def wait_status(self, token, expected, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                if self.status(token) == expected:
                    return
            except OSError:
                pass
            time.sleep(0.05)
        self.log.seek(0)
        self.fail(f'Expected HTTP {expected}; Envoy log:\n{self.log.read().decode()[-5000:]}')

    def check_health(self):
        connection = http.client.HTTPConnection('127.0.0.1', 9901, timeout=2)
        try:
            connection.request('GET', '/ready')
            self.assertEqual(connection.getresponse().status, 200)
        finally:
            connection.close()
        self.assertEqual(self.status(), 403)

    def test_access_logs_only_suppress_the_exact_local_denial_probe(self):
        marker = {'User-Agent': 'code-server-container-healthcheck'}
        cases = [
            ('quiet', {}, 403, False),
            ('remote', {'source': '127.0.0.2', 'headers': {
                **marker, 'X-Forwarded-For': '127.0.0.1'}}, 403, True),
            ('path', {'path': '/proxy/3000/private?secret=do-not-log', 'headers': {
                **marker, 'x-envoy-original-path': '/forged'}}, 403, True),
            ('query', {'path': '/?secret=do-not-log'}, 403, True),
            ('method', {'method': 'POST'}, 403, True),
            ('invalid', {'token': 'garbage'}, 403, True),
            ('signature', {'token': self.token(key=1)}, 403, True),
            ('empty', {'token': ''}, 403, True),
            ('accepted', {'token': self.token()}, 200, True),
            ('ordinary', {'headers': {'User-Agent': 'ordinary-client'}}, 403, True),
        ]
        for name, arguments, expected, _ in cases:
            arguments = dict(arguments)
            arguments['headers'] = {**arguments.get('headers', marker), 'X-Request-ID': name}
            self.assertEqual(self.status(**arguments), expected, name)
        self.stop()  # Flush access logs before asserting both presence and absence.
        self.log.seek(0)
        entries = [json.loads(line) for line in self.log.read().decode().splitlines()
                   if line.startswith('{')]
        records = {entry['request_id']: entry for entry in entries}
        for name, _, expected, logged in cases:
            with self.subTest(name=name):
                if not logged:
                    self.assertNotIn(name, records)
                    continue
                entry = records[name]
                self.assertEqual(int(entry['status']), expected)
                self.assertIn('response_details', entry)
                self.assertIn('duration_ms', entry)
        self.assertTrue(records['remote']['peer'].startswith('127.0.0.2:'))
        self.assertEqual(records['path']['path'], '/proxy/3000/private')
        self.assertEqual(records['path']['denial_reason'], 'missing_assertion')
        self.assertNotIn('do-not-log', json.dumps(entries))
        self.assertNotIn('garbage', json.dumps(entries))

    def test_unexpected_probe_response_is_logged(self):
        def change(config, manager, provider):
            source = next(item['typed_config']['default_source_code']
                          for item in manager['http_filters']
                          if item['typed_config'].get('default_source_code', {}).get('filename')
                          == '/etc/envoy/lua/assertion.lua')
            source['inline_string'] = Path(source.pop('filename')).read_text().replace(
                '[":status"] = "403"', '[":status"] = "503"')
        self.start(change=change, expect_ready=False)
        self.wait_status(None, 503)
        self.assertEqual(self.status(headers={'User-Agent': 'code-server-container-healthcheck',
                                              'X-Request-ID': 'unexpected-probe'}), 503)
        self.stop()
        self.log.seek(0)
        entries = [json.loads(line) for line in self.log.read().decode().splitlines()
                   if line.startswith('{')]
        self.assertTrue(any(entry['request_id'] == 'unexpected-probe' for entry in entries))

    def test_valid_tokens(self):
        for audience in (AUD, [AUD], ['other', AUD]):
            with self.subTest(audience=audience):
                self.assertEqual(self.status(self.token({'aud': audience})), 200)
        self.check_health()

    def test_invalid_tokens(self):
        tokens = [None, 'garbage', 'a.b.c', self.token(key=1),
                  self.token({'iss': 'https://other.cloudflareaccess.com'}),
                  self.token({'aud': ['b' * 64]}), self.token({'aud': ['prefix-' + AUD]}),
                  self.token({'aud': []}), self.token({'aud': None}), self.token({'aud': [{'nested': AUD}]}),
                  self.token({'exp': int(time.time()) - 5}), self.token(omit=['exp']),
                  self.token({'exp': 'invalid'}), self.token({'nbf': int(time.time()) + 300}),
                  self.token({'nbf': 'invalid'}), self.token(algorithm='none'),
                  self.token(algorithm='HS256'), self.token(omit=['aud'])]
        for index, token in enumerate(tokens):
            with self.subTest(case=index):
                self.assertEqual(self.status(token), 403)
        self.assertEqual(self.status(None, '/healthz'), 403)
        self.assertEqual(self.status(None, headers={'Cf-Access-Authenticated-User-Email': 'fake@example.com'}), 403)
        self.assertEqual(self.status(None, headers={'Cookie': 'CF_Authorization=' + self.token()}), 403)
        self.assertEqual(self.status(None, headers={'Authorization': 'Bearer ' + self.token()}), 403)
        self.assertEqual(self.status(None, '/?access_token=' + self.token()), 403)
        self.assertEqual(self.status(self.token({'nbf': int(time.time()) - 30})), 200)

    def test_duplicate_token_headers(self):
        token = self.token()
        for values in ((token, token), (token, 'garbage'), (token + ', ' + token,)):
            with self.subTest(values=len(values)):
                connection = http.client.HTTPConnection('127.0.0.1', 8080, timeout=3)
                try:
                    connection.putrequest('GET', '/')
                    for value in values:
                        connection.putheader('Cf-Access-Jwt-Assertion', value)
                    connection.endheaders()
                    self.assertEqual(connection.getresponse().status, 403)
                finally:
                    connection.close()

    def test_identity_allowlists(self):
        for identities in ([('email', 'user@example.com')], [('common_name', 'service.access')],
                           [('email', 'user@example.com'), ('common_name', 'service.access')]):
            self.start(identities)
            for claims, expected in (({}, 403),
                    ({'email': 'user@example.com'}, 200 if ('email', 'user@example.com') in identities else 403),
                    ({'common_name': 'service.access'}, 200 if ('common_name', 'service.access') in identities else 403),
                    ({'email': 'User@example.com'}, 403), ({'email': 'prefix-user@example.com'}, 403),
                    ({'email': ['user@example.com']}, 403)):
                with self.subTest(identities=identities, claims=claims):
                    self.assertEqual(self.status(self.token(claims)), expected)
            self.assertEqual(self.status(self.token(), headers={'Cf-Access-Authenticated-User-Email': 'user@example.com'}), 403)
            self.assertEqual(self.status(self.token({'email': 'user@example.com'}, key=1)), 403)

    def test_allowlist_whitespace_and_literal_matching(self):
        # Valid local parts such as apostrophes must not stop Envoy from starting.
        self.start(env_override={'CF_ACCESS_ALLOWED_EMAILS': " user+tag@example.com , o'brien&co@example.com "})
        self.assertEqual(self.status(self.token({'email': 'user+tag@example.com'})), 200)
        self.assertEqual(self.status(self.token({'email': "o'brien&co@example.com"})), 200)
        self.assertEqual(self.status(self.token({'email': 'usertag@example.com'})), 403)
        self.assertEqual(self.status(self.token({'email': 'obrien&co@example.com'})), 403)

    def test_rotation_without_process_restart(self):
        pid = self.process.pid
        self.assertEqual(self.status(self.token()), 200)
        self.keys_server.keys = self.jwks  # Cloudflare overlaps old and new keys.
        self.wait_status(self.token(key=1), 200)
        self.assertEqual(self.status(self.token()), 200)
        self.keys_server.keys = [self.jwks[1]]
        self.wait_status(self.token(), 403)
        self.assertEqual(self.status(self.token(key=1)), 200)
        self.assertEqual(self.process.pid, pid)

    def test_key_fetch_failure_fails_closed_and_recovers(self):
        self.keys_server.fail = True
        self.start()
        self.assertEqual(self.status(self.token()), 403)
        self.check_health()  # Listener is ready but authentication remains blocked.
        self.keys_server.fail = False
        self.wait_status(self.token(), 200)
        self.check_health()

    def test_expired_key_cache_does_not_allow_requests_on_fetch_failure(self):
        self.assertEqual(self.status(self.token()), 200)
        self.keys_server.fail = True
        self.wait_status(self.token(), 403)
        self.keys_server.fail = False
        self.wait_status(self.token(), 200)

    def test_jwks_tls_hostname_is_verified(self):
        def wrong_hostname(config, manager, provider):
            cluster = next(item for item in config['static_resources']['clusters']
                           if item['name'] == 'cloudflare_jwks')
            context = cluster['transport_socket']['typed_config']
            context['common_tls_context']['validation_context']['match_typed_subject_alt_names'][0]['matcher']['exact'] = 'wrong.example.com'
        self.start(change=wrong_hostname)
        self.assertEqual(self.status(self.token()), 403)
        self.check_health()  # Listener is ready but authentication remains blocked.

    def test_error_pages_and_unavailable_backend(self):
        status, headers, body = self.response()
        self.assertEqual(status, 403)
        self.assertIn('Cloudflare Access authentication required', body)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(headers['content-type'], 'text/html; charset=utf-8')
        def unavailable(config, manager, provider):
            cluster = next(item for item in config['static_resources']['clusters']
                           if item['name'] == 'code_server')
            cluster['load_assignment']['endpoints'][0]['lb_endpoints'][0]['endpoint']['address']['pipe']['path'] = '/tmp/nonexistent-backend.sock'
        self.start(change=unavailable)
        status, headers, body = self.response(self.token())
        self.assertEqual(status, 503)
        self.assertIn('Service is not ready', body)
        self.assertIn('http-equiv="refresh" content="5"', body)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(self.status(), 403)

    def websocket(self):
        connection = socket.create_connection(('127.0.0.1', 8080), timeout=5)
        connection.sendall(('GET /socket HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\n'
            'Upgrade: websocket\r\nSec-WebSocket-Version: 13\r\n'
            'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n'
            'Cf-Access-Jwt-Assertion: ' + self.token() + '\r\n\r\n').encode())
        response = b''
        while not response.endswith(b'\r\n\r\n'):
            block = connection.recv(1)
            self.assertTrue(block)
            response += block
        self.assertIn(b'101 Switching Protocols', response)
        return connection

    def test_websocket_survives_key_refresh(self):
        with self.websocket() as connection:
            self.keys_server.keys = [self.jwks[1]]
            self.wait_status(self.token(key=1), 200)
            connection.sendall(b'ping')
            self.assertEqual(connection.recv(4), b'ping')

    def test_websocket_maximum_lifetime(self):
        def short_lifetime(config, manager, provider):
            manager['common_http_protocol_options']['max_stream_duration'] = '1s'
        self.start(change=short_lifetime)
        with self.websocket() as connection:
            connection.sendall(b'ping')
            self.assertEqual(connection.recv(4), b'ping')
            self.assertEqual(connection.recv(1), b'')

    def test_missing_and_invalid_settings_prevent_startup(self):
        for name, value in (('CF_ACCESS_TEAM_DOMAIN', ''), ('CF_ACCESS_AUD', ''),
                            ('CF_ACCESS_TEAM_DOMAIN', DOMAIN + '/path'), ('CF_ACCESS_AUD', 'bad'),
                            ('CF_ACCESS_ALLOWED_EMAILS', 'user@example.com,'),
                            ('CF_ACCESS_ALLOWED_EMAILS', 'first last@example.com'),
                            ('CF_ACCESS_ALLOWED_EMAILS', 'user@@example.com'),
                            ('CF_ACCESS_ALLOWED_SERVICE_TOKENS', 'bad identity')):
            with self.subTest(name=name, value=value):
                self.start(env_override={name: value}, expect_ready=False)
                self.assertNotEqual(self.process.wait(timeout=10), 0)
                if name in ('CF_ACCESS_TEAM_DOMAIN', 'CF_ACCESS_AUD'):
                    self.log.seek(0)
                    self.assertIn('Set ' + name, self.log.read().decode())
                with self.assertRaises(OSError):
                    self.status()


if __name__ == '__main__':
    unittest.main(verbosity=2)
