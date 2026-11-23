#!/bin/sh
set -eu

# Docker supplies /dev/console with a TTY. Without it, append: would create a file.
if [ ! -c /dev/console ]; then
    mkdir -p "$1/envoy.service.d"
    printf '[Service]\nStandardOutput=journal\nStandardError=journal\n' > "$1/envoy.service.d/console.conf"
fi
