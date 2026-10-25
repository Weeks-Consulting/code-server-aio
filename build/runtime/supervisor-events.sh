#!/bin/bash
# Exit Supervisor when any service exhausts its startup retries.
set -euf -o pipefail
export LC_ALL=C

while true; do
    # stdout is reserved for Supervisor's event listener protocol.
    printf 'READY\n'
    IFS= read -r headers || exit 1
    length=
    for field in $headers; do
        case "$field" in len:*) length=${field#len:} ;; esac
    done
    [[ "$length" =~ ^[0-9]+$ ]] || exit 1
    payload=
    if (( length > 0 )); then
        IFS= read -r -N "$length" payload || exit 1
    fi
    printf 'RESULT 2\nOK'

    name=
    for field in $payload; do
        case "$field" in processname:*) name=${field#processname:} ;; esac
    done
    if [[ -n "$name" ]]; then
        printf 'Critical service %s entered FATAL; shutting down for container restart\n' "$name" >&2
        kill -TERM "$PPID"
    fi
done
