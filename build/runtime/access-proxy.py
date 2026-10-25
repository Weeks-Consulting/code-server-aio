"""Refresh Cloudflare Access public keys and generate a fail-closed HAProxy config.

JWT validation stays in HAProxy; this helper is never on the request path.
"""

import base64
import hashlib
import json
import os
import pwd
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


RUNTIME = Path("/run/access-proxy")
REFRESH_SECONDS = 3600
MAX_KEY_AGE_SECONDS = 86400


def settings():
    domain = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "")
    audience = os.environ.get("CF_ACCESS_AUD", "")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.cloudflareaccess\.com", domain):
        raise ValueError("Set CF_ACCESS_TEAM_DOMAIN to your team.cloudflareaccess.com hostname")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", audience):
        raise ValueError("Set CF_ACCESS_AUD to the 64-character application audience")
    return "https://" + domain, audience


def number(value):
    """Decode a base64url JWK integer."""
    return int.from_bytes(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)), "big")


def public_keys(document):
    """Convert RSA signing JWKs to immutable PEM files for HAProxy."""
    paths = []
    for key in document["keys"]:
        if key.get("kty") != "RSA" or key.get("use", "sig") != "sig" or key.get("alg", "RS256") != "RS256":
            continue
        public = rsa.RSAPublicNumbers(number(key["e"]), number(key["n"])).public_key()
        if public.key_size < 2048:
            print("Skipping an RSA signing key smaller than 2048 bits", file=sys.stderr, flush=True)
            continue
        pem = public.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        path = RUNTIME / (hashlib.sha256(pem).hexdigest() + ".pem")
        path.write_bytes(pem)
        paths.append(path)
    if not paths:
        raise ValueError("No RSA signing keys in the Cloudflare response")
    return sorted(set(paths))


def allowed_identities():
    """Only permit literal HAProxy string match operands, never config syntax."""
    values = []
    for name, pattern in (
        ("CF_ACCESS_ALLOWED_EMAILS", r"[A-Za-z0-9_][A-Za-z0-9_+.-]*@[A-Za-z0-9.-]+"),
        ("CF_ACCESS_ALLOWED_SERVICE_TOKENS", r"[A-Za-z0-9_][A-Za-z0-9_.@-]*"),
    ):
        raw = os.environ.get(name, "")
        entries = tuple(value.strip() for value in raw.split(",")) if raw else ()
        if any(not re.fullmatch(pattern, value) for value in entries):
            raise ValueError(f"{name} must be a comma-separated list of literal identities")
        values.append(entries)
    return tuple(values)


def require_haproxy():
    version = subprocess.run(["/usr/sbin/haproxy", "-v"], check=True, capture_output=True, text=True).stdout
    match = re.search(r"HAProxy version (\d+)\.(\d+)", version)
    if not match or int(match[1]) < 3:
        raise ValueError("Origin JWT validation requires HAProxy 3.0 or newer")


def configuration(issuer=None, audience=None, keys=(), valid_until=0, identities=((), ())):
    config = """global
    log stdout format raw local0
    user haproxy
    group haproxy
    maxconn 1024
    nbthread 2
    hard-stop-after 1h

defaults
    mode http
    log global
    option httplog
    timeout http-request 10s
    timeout connect 5s
    timeout client 1h
    timeout server 1h
    timeout tunnel 1h

frontend access
    bind 0.0.0.0:8080
    http-error status 403 content-type "text/html; charset=utf-8" file /etc/haproxy/errors/access-403.html hdr Cache-Control no-store hdr X-Content-Type-Options nosniff
    http-error status 503 content-type "text/html; charset=utf-8" file /etc/haproxy/errors/access-503.html hdr Cache-Control no-store hdr X-Content-Type-Options nosniff
"""
    if not keys:
        return config + "    http-request deny deny_status 503\n"

    # Validate even direct callers before interpolating into HAProxy syntax.
    if not isinstance(issuer, str) or not re.fullmatch(r"https://[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.cloudflareaccess\.com", issuer):
        raise ValueError("Invalid Access issuer")
    if not isinstance(audience, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", audience):
        raise ValueError("Invalid Access audience")
    if not isinstance(valid_until, int) or valid_until < 0:
        raise ValueError("Invalid key expiry")
    for key in keys:
        if key.parent != RUNTIME or not re.fullmatch(r"[a-f0-9]{64}\.pem", key.name):
            raise ValueError("Invalid signing key path")
    emails, services = identities
    for entries, pattern in ((emails, r"[A-Za-z0-9_][A-Za-z0-9_+.-]*@[A-Za-z0-9.-]+"), (services, r"[A-Za-z0-9_][A-Za-z0-9_.@-]*")):
        if any(not re.fullmatch(pattern, value) for value in entries):
            raise ValueError("Invalid allowed identity")

    config += f"""    # Every route, including /healthz, requires a valid Access JWT.
    http-request deny deny_status 503 unless {{ date -m int lt {valid_until} }}
    http-request deny unless {{ req.hdr_cnt(Cf-Access-Jwt-Assertion) eq 1 }}
    http-request set-var(txn.token) req.hdr(Cf-Access-Jwt-Assertion)
    http-request deny unless {{ var(txn.token),jwt_header_query('$.alg') -m str RS256 }}
    http-request deny unless {{ var(txn.token),jwt_payload_query('$.iss') -m str {issuer} }}
    # Cloudflare uses an array of audiences; also accept the standard string form.
    acl expected_audience var(txn.token),jwt_payload_query('$.aud') -m str {audience}
    acl expected_audience var(txn.token),jwt_payload_query('$.aud') -m reg '^[[][[:space:]]*("[^"]*"[[:space:]]*,[[:space:]]*)*"{audience}"([[:space:]]*,[[:space:]]*"[^"]*")*[[:space:]]*[]]$'
    http-request deny unless expected_audience
    http-request set-var(txn.now) date
    http-request deny unless {{ var(txn.token),jwt_payload_query('$.exp','int'),sub(txn.now) -m int gt 0 }}
    acl has_nbf var(txn.token),jwt_payload_query('$.nbf') -m found
    http-request deny if has_nbf !{{ var(txn.token),jwt_payload_query('$.nbf','int') -m int gt 0 }}
    http-request deny if has_nbf !{{ var(txn.token),jwt_payload_query('$.nbf','int'),sub(txn.now) -m int le 0 }}
"""
    # Repeating an ACL name ORs the conditions: accept any currently trusted key.
    for key in keys:
        config += f"    acl valid_signature var(txn.token),jwt_verify(RS256,{key}) -m int 1\n"
    config += "    http-request deny unless valid_signature\n"
    # Match only signed JWT claims, never the forwarded identity headers.
    for claim, entries in (("email", emails), ("common_name", services)):
        if entries:
            config += f"    acl allowed_identity var(txn.token),jwt_payload_query('$.{claim}') -m str {' '.join(entries)}\n"
    if emails or services:
        config += "    http-request deny unless allowed_identity\n"
    config += """    default_backend code_server

backend code_server
    server code_server 127.0.0.1:8081
"""
    return config


def install(config, reload=True):
    candidate = RUNTIME / "haproxy.cfg.new"
    candidate.write_text(config)
    subprocess.run(["/usr/sbin/haproxy", "-c", "-f", str(candidate)], check=True, capture_output=True)
    candidate.replace(RUNTIME / "haproxy.cfg")
    if reload:
        # The master CLI waits for reload completion, avoiding overlapping reloads.
        # Existing WebSockets continue until the old worker hits hard-stop-after.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(30)
            connection.connect(str(RUNTIME / "master.sock"))
            connection.sendall(b"reload\n")
            response = b""
            while block := connection.recv(65536):
                response += block
        if b"Success=1" not in response:
            raise RuntimeError("HAProxy reload failed: " + response.decode(errors="replace"))


def refresh():
    issuer, audience = settings()
    identities = allowed_identities()
    with urllib.request.urlopen(issuer + "/cdn-cgi/access/certs", timeout=20) as response:
        document = json.loads(response.read(1024 * 1024))
    keys = public_keys(document)
    install(configuration(issuer, audience, keys, int(time.time()) + MAX_KEY_AGE_SECONDS, identities))
    for path in RUNTIME.glob("*.pem"):
        if re.fullmatch(r"[a-f0-9]{64}\.pem", path.name) and path not in keys:
            path.unlink()
    print(f"Access signing keys refreshed ({len(keys)} keys)", flush=True)


def main():
    RUNTIME.mkdir(mode=0o755, parents=True, exist_ok=True)
    if sys.argv[1:] == ["--init"]:
        require_haproxy()
        account = pwd.getpwnam("haproxy")
        os.chown(RUNTIME, account.pw_uid, account.pw_gid)
        install(configuration(), reload=False)
        return
    startup_retries = 0
    while True:
        try:
            refresh()
        except Exception as error:
            print(f"Access key refresh failed; access remains restricted: {error}", file=sys.stderr, flush=True)
            startup_retries += 1
            socket_not_ready = isinstance(error, (FileNotFoundError, ConnectionRefusedError))
            time.sleep(1 if socket_not_ready and startup_retries <= 10 else 60)
        else:
            startup_retries = 0
            time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
