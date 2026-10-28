#!/bin/sh
set -eu
# A stopped process can leave its Unix socket behind after a container restart.
rm -f /run/code-server/backend.sock
exec /usr/bin/code-server --socket /run/code-server/backend.sock --socket-mode 660 --auth none /home/cloud-user
