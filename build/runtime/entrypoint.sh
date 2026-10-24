#!/bin/bash
set -euo pipefail

if [[ ! "${HOMEBREW_INSTALL_TIMEOUT:-600}" =~ ^[1-9][0-9]*$ ]]; then
    echo "HOMEBREW_INSTALL_TIMEOUT must be a positive number of seconds" >&2
    exit 1
fi

# Always start with a deny-all proxy, including after container restarts.
/usr/bin/python3 /usr/local/lib/access-proxy.py --init

exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf "$@"
