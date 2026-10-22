"""Integration tests: run in the image with no host ports or Cloudflare credentials."""

import base64
import hashlib
import http.client
import http.server
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import unittest.mock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


spec = importlib.util.spec_from_file_location("access_proxy", "/usr/local/lib/access-proxy.py")
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)
AUD = "a" * 64
ISSUER = "https://test.cloudflareaccess.com"


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def jwk(private):
    numbers = private.public_key().public_numbers()
    encode = lambda number: b64(number.to_bytes((number.bit_length() + 7) // 8, "big"))
    return {"kty": "RSA", "alg": "RS256", "n": encode(numbers.n), "e": encode(numbers.e)}


class Backend(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", b64(hashlib.sha1(
                (self.headers["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()) + "=")
            self.end_headers()
            self.wfile.write(self.rfile.read(4))
            self.wfile.flush()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"backend reached")

    def log_message(self, *args):
        pass


class AccessProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        proxy.RUNTIME = Path(cls.directory.name)
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.keys = proxy.public_keys({"keys": [jwk(cls.key)]})
        cls.backend = http.server.ThreadingHTTPServer(("127.0.0.1", 8081), Backend)
        threading.Thread(target=cls.backend.serve_forever, daemon=True).start()
        proxy.install(proxy.configuration(), reload=False)
        cls.log = tempfile.TemporaryFile()
        cls.process = subprocess.Popen([
            "haproxy", "-W", "-db", "-f", str(proxy.RUNTIME / "haproxy.cfg"),
            "-p", str(proxy.RUNTIME / "haproxy.pid"),
            "-S", str(proxy.RUNTIME / "master.sock") + ",mode,600",
        ], stdout=cls.log, stderr=cls.log)
        cls.wait_status(None, 503)

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.backend.shutdown()
        cls.backend.server_close()
        cls.log.close()
        cls.directory.cleanup()

    @staticmethod
    def response(token=None, path="/", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=2)
        request_headers = {} if headers is None else dict(headers)
        if token is not None:
            request_headers["Cf-Access-Jwt-Assertion"] = token
        try:
            connection.request("GET", path, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            connection.close()

    @staticmethod
    def status(token=None, path="/", headers=None):
        return AccessProxyTests.response(token, path, headers)[0]

    @classmethod
    def wait_status(cls, token, expected):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if cls.status(token) == expected:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        cls.log.seek(0)
        log = cls.log.read().decode()
        raise AssertionError(f"Proxy did not return {expected}; last status {cls.status(token)}\n{log[-5000:]}")

    @classmethod
    def token(cls, changes=None, key=None, algorithm="RS256", omit=()):
        payload = {"iss": ISSUER, "aud": [AUD], "exp": int(time.time()) + 300}
        payload.update(changes or {})
        for claim in omit:
            payload.pop(claim, None)
        message = ".".join(b64(json.dumps(part).encode()) for part in [
            {"alg": algorithm, "typ": "JWT"}, payload,
        ])
        signature = (key or cls.key).sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
        return message + "." + b64(signature)

    def setUp(self):
        proxy.install(proxy.configuration(ISSUER, AUD, self.keys, int(time.time()) + 3600))
        self.wait_status(self.token(), 200)

    def test_valid_tokens(self):
        for audience in [AUD, [AUD], ["other", AUD]]:
            with self.subTest(audience=audience):
                self.assertEqual(self.status(self.token({"aud": audience})), 200)

    def test_invalid_tokens(self):
        tokens = [
            None, "garbage", "a.b.c", self.token(key=self.other_key),
            self.token({"iss": "https://other.cloudflareaccess.com"}),
            self.token({"aud": ["b" * 64]}), self.token({"aud": ["prefix-" + AUD]}),
            self.token({"aud": None}), self.token({"aud": []}),
            self.token({"aud": [{"nested": AUD}]}),
            self.token({"exp": int(time.time()) - 1}), self.token(omit=["exp"]),
            self.token({"exp": "invalid"}), self.token({"nbf": int(time.time()) + 300}),
            self.token({"nbf": "invalid"}), self.token(algorithm="none"),
            self.token(algorithm="HS256"), self.token(omit=["aud"]),
        ]
        for index, token in enumerate(tokens):
            with self.subTest(case=index):
                self.assertEqual(self.status(token), 403)
        self.assertEqual(self.status(None, "/healthz"), 403)
        self.assertEqual(self.status(None, headers={"Cf-Access-Authenticated-User-Email": "fake@example.com"}), 403)

    def test_duplicate_token_headers(self):
        token = self.token()
        for values in ((token, token), (token, "garbage"), (token + ", " + token,)):
            with self.subTest(values=len(values)):
                connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=2)
                try:
                    connection.putrequest("GET", "/")
                    for value in values:
                        connection.putheader("Cf-Access-Jwt-Assertion", value)
                    connection.endheaders()
                    self.assertEqual(connection.getresponse().status, 403)
                finally:
                    connection.close()

    def test_public_key_filters(self):
        valid = jwk(self.key)
        ignored = [{"kty": "EC"}, dict(valid, use="enc"), dict(valid, alg="RS512")]
        self.assertEqual(proxy.public_keys({"keys": ignored + [valid]}), self.keys)
        with self.assertRaises(ValueError):
            proxy.public_keys({"keys": ignored})
        small = jwk(rsa.generate_private_key(public_exponent=65537, key_size=1024))
        for keys in ([small], [valid, small]):
            with self.assertRaises(ValueError):
                proxy.public_keys({"keys": keys})

    def test_not_before_in_past(self):
        self.assertEqual(self.status(self.token({"nbf": int(time.time()) - 30})), 200)

    def test_stale_keys_deny_all(self):
        proxy.install(proxy.configuration(ISSUER, AUD, self.keys, int(time.time()) - 1))
        self.wait_status(self.token(), 503)
        self.assert_error_page(503, "Access validation is not ready", self.token())

    def assert_error_page(self, expected, title, token=None):
        status, headers, body = self.response(token)
        self.assertEqual(status, expected)
        self.assertEqual(headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertIn(title, body)
        self.assertIn("CF_ACCESS_TEAM_DOMAIN", body)
        self.assertIn("CF_ACCESS_AUD", body)
        self.assertNotIn("backend reached", body)

    def test_custom_forbidden_page(self):
        self.assert_error_page(403, "Cloudflare Access authentication required")
        self.assert_error_page(403, "Cloudflare Access authentication required", "invalid-token")

    def test_rotation(self):
        new_keys = proxy.public_keys({"keys": [jwk(self.other_key)]})
        proxy.install(proxy.configuration(ISSUER, AUD, new_keys, int(time.time()) + 3600))
        self.wait_status(self.token(key=self.other_key), 200)
        self.assertEqual(self.status(self.token()), 403)

    def test_websocket_survives_reload(self):
        with socket.create_connection(("127.0.0.1", 8080), timeout=3) as connection:
            connection.sendall((
                "GET /socket HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\n"
                "Upgrade: websocket\r\nSec-WebSocket-Version: 13\r\n"
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                "Cf-Access-Jwt-Assertion: " + self.token() + "\r\n\r\n"
            ).encode())
            response = b""
            while not response.endswith(b"\r\n\r\n"):
                response += connection.recv(1)
            self.assertIn(b"101 Switching Protocols", response)
            proxy.install(proxy.configuration(ISSUER, AUD, self.keys, int(time.time()) + 3600))
            self.wait_status(self.token(), 200)
            connection.sendall(b"ping")
            self.assertEqual(connection.recv(4), b"ping")

    def test_missing_settings(self):
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                proxy.settings()

    def test_refresh_downloads_keys(self):
        import io
        response = io.BytesIO(json.dumps({"keys": [jwk(self.key)]}).encode())
        with unittest.mock.patch.dict(os.environ, {
            "CF_ACCESS_TEAM_DOMAIN": "test.cloudflareaccess.com", "CF_ACCESS_AUD": AUD,
        }), unittest.mock.patch.object(proxy.urllib.request, "urlopen", return_value=response) as fetch:
            proxy.refresh()
            fetch.assert_called_once_with(ISSUER + "/cdn-cgi/access/certs", timeout=20)
        self.assertEqual(self.status(self.token()), 200)

    def test_refresh_prunes_only_after_success(self):
        import io
        with tempfile.TemporaryDirectory() as directory, unittest.mock.patch.object(proxy, "RUNTIME", Path(directory)), unittest.mock.patch.dict(os.environ, {
            "CF_ACCESS_TEAM_DOMAIN": "test.cloudflareaccess.com", "CF_ACCESS_AUD": AUD,
        }):
            old = proxy.public_keys({"keys": [jwk(self.other_key)]})[0]
            sentinel = proxy.RUNTIME / "manual.pem"
            sentinel.write_text("preserve files not managed by the refresher")
            document = json.dumps({"keys": [jwk(self.key)]}).encode()
            with unittest.mock.patch.object(proxy.urllib.request, "urlopen", return_value=io.BytesIO(document)), unittest.mock.patch.object(proxy, "install", side_effect=RuntimeError("reload failed")):
                with self.assertRaises(RuntimeError):
                    proxy.refresh()
            self.assertTrue(old.exists())
            with unittest.mock.patch.object(proxy.urllib.request, "urlopen", return_value=io.BytesIO(document)), unittest.mock.patch.object(proxy, "install"):
                proxy.refresh()
            self.assertFalse(old.exists())
            self.assertTrue(sentinel.exists())
            self.assertEqual(len(list(proxy.RUNTIME.glob("*.pem"))), 2)

    def test_socket_retry_backoff_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory, unittest.mock.patch.object(proxy, "RUNTIME", Path(directory)), unittest.mock.patch.object(proxy.sys, "argv", ["access-proxy.py"]), unittest.mock.patch.object(proxy, "refresh", side_effect=[FileNotFoundError("socket not ready")] * 12 + [None, KeyboardInterrupt]), unittest.mock.patch.object(proxy.time, "sleep") as sleep, unittest.mock.patch.object(proxy.sys, "stderr"):
            with self.assertRaises(KeyboardInterrupt):
                proxy.main()
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [1] * 10 + [60, 60, proxy.REFRESH_SECONDS])

    def test_bootstrap_blocks_every_route(self):
        proxy.install(proxy.configuration())
        self.assertEqual(self.status(self.token()), 503)
        self.assertEqual(self.status(None, "/healthz"), 503)
        self.assert_error_page(503, "Access validation is not ready")


if __name__ == "__main__":
    unittest.main(verbosity=2)
