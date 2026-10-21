#!/usr/bin/python3
"""Readiness: all services running, nested Docker ready, proxy enforcing Access."""

import subprocess
import sys
import urllib.error
import urllib.request


try:
    status = subprocess.run(["/usr/bin/supervisorctl", "status"], check=True,
                            capture_output=True, text=True, timeout=3)
    processes = {parts[0]: parts[1] for line in status.stdout.splitlines()
                 if len(parts := line.split()) >= 2}
    for name in ("dockerd", "code-server", "haproxy", "access-keys", "service-events"):
        if processes.get(name) != "RUNNING":
            raise RuntimeError(f"{name} is not running")
    subprocess.run(["/usr/bin/docker", "info", "--format", "{{.ServerVersion}}"],
                   check=True, capture_output=True, timeout=3)
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with http.open("http://127.0.0.1:8081/healthz", timeout=2) as response:
        if response.status != 200:
            raise RuntimeError("code-server is not ready")
    try:
        http.open("http://127.0.0.1:8080/", timeout=2)
    except urllib.error.HTTPError as error:
        if error.code != 403:
            raise RuntimeError(f"Access proxy returned {error.code}, expected 403") from error
    else:
        raise RuntimeError("Access proxy did not reject an unauthenticated request")
except Exception as error:
    print(f"Not ready: {error}", file=sys.stderr)
    sys.exit(1)
