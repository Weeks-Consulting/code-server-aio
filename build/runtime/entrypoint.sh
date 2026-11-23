#!/bin/bash
# Prepare mounted directories before starting any services.
set -euo pipefail

# Root setup must never resolve commands from the user's mounted directories.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export HOME=/root USER=root LOGNAME=root

legacy=false
if [[ $PPID == 1 && $(< /proc/1/comm) == docker-init ]]; then
    legacy=true
fi
if [[ $legacy == false ]] && ! grep -qx '0::/' /proc/self/cgroup; then
    echo 'Error: the workspace requires cgroup v2 and a private cgroup namespace.' >&2
    exit 1
fi

# Without --privileged, Docker mounts cgroups read-only; nested Docker needs them writable.
# Change only this mount's flags; a filesystem-wide remount can be denied in a user namespace.
if ! mount -o remount,bind,rw /sys/fs/cgroup; then
    echo 'Error: could not make /sys/fs/cgroup writable; nested Docker requires it. Check the workspace container capabilities and security options.' >&2
    exit 1
fi

user_entry=$(getent passwd cloud-user)
IFS=: read -r _ _ uid gid _ home _ <<< "$user_entry"
mkdir -p "$home"
# Preserve ownership of existing contents, including nested-container files.
chown "$uid:$gid" "$home"

if [[ $legacy == true ]]; then
    echo 'Starting with legacy Docker init. Use manage upgrade to enable systemd and Homebrew services.' >&2
    /usr/bin/systemd-tmpfiles --create /etc/tmpfiles.d/workspace.conf
    exec /usr/bin/python3 -I /usr/local/lib/legacy-supervisor.py -c /etc/supervisor/supervisord.conf "$@"
fi

exec /usr/lib/systemd/systemd "$@"
