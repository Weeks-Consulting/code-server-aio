#!/bin/sh
# Check shell syntax and Compose configurations without using .env.
set -eu
export PYTHONDONTWRITEBYTECODE=1
cd -- "$(dirname -- "$0")/../.."

python3 build/tools/dependencies.py check

for script in manage build/tools/*.sh build/tests/*.sh; do sh -n "$script"; done
for script in build/runtime/*.sh build/ci/*.sh; do bash -n "$script"; done
command -v shellcheck >/dev/null || { echo 'Install shellcheck to run checks.' >&2; exit 1; }
shellcheck --severity=style manage build/tools/*.sh build/tests/*.sh build/runtime/*.sh build/ci/*.sh
python3 -m unittest discover -s build/tests -p "test_*.py"

# Configuration checks use a disposable passphrase, never a deployment key.
export BACKUP_GPG_PASSPHRASE=validation-only

# The base file must work with only the tunnel token set.
CF_TUNNEL_TOKEN=validation-only docker compose --env-file /dev/null config --quiet
for backup in "" compose.backup.yaml compose.backup-local.yaml; do
    for build in "" compose.build.yaml; do
        set -- -f compose.yaml
        if [ -n "$backup" ]; then set -- "$@" -f compose.backup-common.yaml; fi
        for file in $build $backup; do set -- "$@" -f "$file"; done
        docker compose --env-file .env.example "$@" config --quiet
    done
done

# Validate the test-only RustFS override without starting containers.
docker compose --env-file .env.example -f compose.yaml -f compose.backup-common.yaml -f compose.backup.yaml \
    -f build/tests/compose.backup-s3.yaml config --quiet

echo 'Syntax and Compose checks passed.'
