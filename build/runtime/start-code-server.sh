#!/bin/bash
# Initialize persistent tools behind the proxy's startup page, then drop privileges.
set -euo pipefail
# Infrastructure must never execute user-installed Homebrew commands.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
initialization_failed() {
    local result=$?
    if (( result != 0 )); then
        echo "Workspace initialization failed ($result); restarting the container." >&2
        /usr/bin/supervisorctl shutdown || true
    fi
}
trap initialization_failed EXIT

proxy_deadline=$((SECONDS + 120))
while true; do
    status=$(/usr/bin/curl --noproxy '*' --silent --output /dev/null --write-out '%{http_code}' \
    --max-time 1 http://127.0.0.1:8080/ || true)
    [[ $status == 503 || $status == 403 ]] && break
    if (( SECONDS >= proxy_deadline )); then
        echo "Access proxy did not become ready within 120 seconds (last HTTP status: $status)." >&2
        exit 1
    fi
    sleep 0.1
done

# Bind mounts on /home may be empty; create a writable home for cloud-user.
# Leave existing contents alone so nested containers retain their file owners.
mkdir -p /home/cloud-user
/usr/bin/chown cloud-user:cloud-user /home/cloud-user

# Install after /home is mounted so an empty bind mount also works.
if [ ! -x /home/linuxbrew/.linuxbrew/bin/brew ] || [ -f /home/linuxbrew/.homebrew-installing ]; then
    echo "Installing Homebrew for cloud-user (first startup only)..."
    install -d -o cloud-user -g cloud-user /home/linuxbrew /home/linuxbrew/.linuxbrew
    # Keep this marker on failure so interrupted installations are retried.
    touch /home/linuxbrew/.homebrew-installing
    /usr/bin/timeout --signal=TERM --kill-after=30s "${HOMEBREW_INSTALL_TIMEOUT:-600}s" \
        /usr/bin/sudo -H -u cloud-user env PATH="$PATH" NONINTERACTIVE=1 \
        /bin/bash /usr/local/lib/homebrew-install.sh
    /usr/bin/sudo -H -u cloud-user /home/linuxbrew/.linuxbrew/bin/brew --version
    rm -f /home/linuxbrew/.homebrew-installing
fi

# Add one-time startup commands here; define services in supervisord.conf.

# Replace this process so Supervisor tracks code-server itself as cloud-user.
exec /usr/bin/python3 -c '
import os, pwd
user = pwd.getpwnam("cloud-user")
os.initgroups(user.pw_name, user.pw_gid)
os.setgid(user.pw_gid)
os.setuid(user.pw_uid)
os.chdir(user.pw_dir)
os.environ["PATH"] = "/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
os.execv("/usr/bin/code-server", ["code-server", "--bind-addr", "127.0.0.1:8081", "--auth", "none", user.pw_dir])
'
