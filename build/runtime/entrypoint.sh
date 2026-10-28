#!/bin/bash
# Prepare mounted directories before starting any services.
set -euo pipefail

# Root setup must never resolve commands from the user's mounted directories.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# The backend socket stays inside the combined code-server and Envoy container.
mkdir -p /run/code-server
chown 10000:10000 /run/code-server
chmod 0750 /run/code-server

user_entry=$(getent passwd cloud-user)
IFS=: read -r _ _ uid gid _ home _ <<< "$user_entry"
mkdir -p "$home"
# Preserve ownership of existing contents, including nested-container files.
chown "$uid:$gid" "$home"

exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf "$@"
