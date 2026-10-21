#!/bin/bash
set -euo pipefail

# Bind mounts on /home may be empty; create the home before repairing ownership.
mkdir -p /home/cloud-user

# Repair ownership for both named volumes and bind mounts.
case "${REPAIR_HOME_OWNERSHIP:-true}" in
    true) /usr/bin/chown -R cloud-user:cloud-user /home/cloud-user /workspace ;;
    false) /usr/bin/chown cloud-user:cloud-user /home/cloud-user /workspace ;;
    *) echo "REPAIR_HOME_OWNERSHIP must be true or false" >&2; exit 1 ;;
esac

# Install after /home is mounted so an empty bind mount also works.
if [ ! -x /home/linuxbrew/.linuxbrew/bin/brew ] || [ -f /home/linuxbrew/.homebrew-installing ]; then
    echo "Installing Homebrew for cloud-user (first startup only)..."
    install -d -o cloud-user -g cloud-user /home/linuxbrew /home/linuxbrew/.linuxbrew
    # Keep this marker on failure so interrupted installations are retried.
    touch /home/linuxbrew/.homebrew-installing
    if [[ ! "${HOMEBREW_INSTALL_TIMEOUT:-600}" =~ ^[1-9][0-9]*$ ]]; then
        echo "HOMEBREW_INSTALL_TIMEOUT must be a positive number of seconds" >&2
        exit 1
    fi
    /usr/bin/timeout --signal=TERM --kill-after=30s "${HOMEBREW_INSTALL_TIMEOUT:-600}s" \
        /usr/bin/sudo -H -u cloud-user env PATH="$PATH" NONINTERACTIVE=1 /bin/bash <<'HOMEBREW_INSTALL'
set -euo pipefail
installer=$(mktemp)
trap 'rm -f "$installer"' EXIT
/usr/bin/curl --fail --silent --show-error --location --retry 3 --connect-timeout 10 --max-time 60 \
    https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh -o "$installer"
/bin/bash "$installer"
/home/linuxbrew/.linuxbrew/bin/brew --version
HOMEBREW_INSTALL
    rm -f /home/linuxbrew/.homebrew-installing
fi

# Add one-time startup commands here; define services in supervisord.conf.

# Always start with a deny-all proxy, including after container restarts.
/usr/bin/python3 /usr/local/lib/access-proxy.py --init

exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf "$@"
