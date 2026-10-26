#!/bin/bash
# Install workspace prerequisites; called by initialize-workspace.py.
set -euo pipefail

install_timeout=${HOMEBREW_INSTALL_TIMEOUT:-600}
if [[ ! "$install_timeout" =~ ^[1-9][0-9]*$ ]]; then
    echo "HOMEBREW_INSTALL_TIMEOUT must be a positive number of seconds" >&2
    exit 1
fi

# Install after /home is mounted so an empty bind mount also works.
if [ ! -x /home/linuxbrew/.linuxbrew/bin/brew ] || [ -f /home/linuxbrew/.homebrew-installing ]; then
    echo "Installing Homebrew for cloud-user (first startup only)..."
    install -d -o cloud-user -g cloud-user /home/linuxbrew /home/linuxbrew/.linuxbrew
    # Keep this marker on failure so interrupted installations are retried.
    touch /home/linuxbrew/.homebrew-installing
    /usr/bin/timeout --signal=TERM --kill-after=30s "${install_timeout}s" \
        /usr/bin/sudo -H -u cloud-user env PATH="$PATH" NONINTERACTIVE=1 \
        /bin/bash /usr/local/lib/homebrew-install.sh
    /usr/bin/sudo -H -u cloud-user /home/linuxbrew/.linuxbrew/bin/brew --version
    rm -f /home/linuxbrew/.homebrew-installing
fi

# Add other prerequisite installation steps here.
