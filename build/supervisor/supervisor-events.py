"""Exit Supervisor when a required service exhausts its startup retries."""

import os
import signal
import sys

from supervisor import childutils


CRITICAL = {"dockerd", "code-server", "haproxy", "access-keys"}

while True:
    headers, payload = childutils.listener.wait(sys.stdin, sys.stdout)
    fields = childutils.get_headers(payload)
    childutils.listener.ok(sys.stdout)
    if fields.get("processname") in CRITICAL:
        print(f"Critical service {fields['processname']} entered FATAL; shutting down for container restart",
              file=sys.stderr, flush=True)
        os.kill(os.getppid(), signal.SIGTERM)
