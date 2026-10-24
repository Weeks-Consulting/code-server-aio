#!/bin/bash
# Initialize a deployment's backup passphrase without executing its .env file.
set -euo pipefail
env_file=${1:?Pass the deployment .env file}
[[ -f $env_file && ! -L $env_file ]] || {
    echo 'Backup key initialization requires a regular .env file.' >&2; exit 1;
}
# Serialize initializers before inspecting the current file, so simultaneous
# launches cannot replace each other's newly generated recovery key.
exec {key_lock}< "$env_file"
flock --exclusive "$key_lock"
chmod 600 -- "$env_file"
# Compose uses the last assignment. Recognize missing and explicitly empty values,
# including export, quoted empty strings, and trailing comments.
pattern='^[[:space:]]*(export[[:space:]]+)?GPG_PASSPHRASE[[:space:]]*='
if awk -v pattern="$pattern" '
    $0 ~ pattern {
        value = $0
        sub(pattern, "", value)
        sub(/[[:space:]]+#.*$/, "", value)
        sub(/^[[:space:]]+/, "", value)
        sub(/[[:space:]]+$/, "", value)
    }
    END { exit !(value != "" && value != "\042\042" && value != "\047\047" && value !~ /^#/) }
' "$env_file"; then
    exit 0
fi
umask 077
# 256 bits from the OS CSPRNG; hexadecimal is safe in Compose .env syntax.
key=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
[[ $key =~ ^[a-f0-9]{64}$ ]] || { echo 'Unable to generate backup key.' >&2; exit 1; }
temporary=$(mktemp "${env_file}.XXXXXXXX")
trap 'rm -f -- "$temporary"' EXIT
# Preserve ownership when an administrator refreshes a user-owned deployment.
cp -p -- "$env_file" "$temporary"
chmod 600 -- "$temporary"
awk -v pattern="$pattern" -v key="$key" '
    $0 ~ pattern { print "GPG_PASSPHRASE=" key; found = 1; next }
    { print }
    END { if (!found) print "\nGPG_PASSPHRASE=" key }
' "$env_file" > "$temporary"
mv -- "$temporary" "$env_file"
echo 'Generated backup encryption key in .env. Save it separately in a password manager; it is required for recovery.'
