#!/bin/bash
# Prepare the mounted workspace before starting code-server through Supervisor.
set -euo pipefail

# Root setup must never resolve commands from the user's mounted directories.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

initialization_failed() {
    local result=$?
    printf 'Workspace initialization failed (%s); restarting the container.\n' "$result" >&2
    /usr/bin/supervisorctl shutdown || true
    exit 1
}
trap initialization_failed ERR

# The backend socket stays inside the combined code-server and Envoy container.
mkdir -p /run/code-server
chown 10000:10000 /run/code-server
chmod 0750 /run/code-server

user_entry=$(getent passwd cloud-user)
IFS=: read -r _ _ uid gid _ home _ <<< "$user_entry"
mkdir -p "$home"
# Preserve ownership of existing contents, including nested-container files.
chown "$uid:$gid" "$home"

/usr/bin/supervisorctl start code-server
