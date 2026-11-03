#!/bin/bash
# Prepare mounted directories before starting any services.
set -euo pipefail

# Root setup must never resolve commands from the user's mounted directories.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Without --privileged, Docker mounts cgroups read-only; nested Docker needs them writable.
# Change only this mount's flags; a filesystem-wide remount can be denied in a user namespace.
if ! mount -o remount,bind,rw /sys/fs/cgroup; then
    echo 'Error: could not make /sys/fs/cgroup writable; nested Docker requires it. Check the workspace container capabilities and security options.' >&2
    exit 1
fi

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
