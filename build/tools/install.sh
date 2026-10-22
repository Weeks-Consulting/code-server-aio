#!/bin/bash
# Install or refresh a published Compose deployment without building the project.
set -euo pipefail

repository=Weeks-Consulting/code-server-aio
directory=/opt/code-server
version=
while (( $# )); do
    case "$1" in
        --version|--directory|--repository)
            if (( $# < 2 )); then echo "Missing value for $1" >&2; exit 1; fi
            case "$1" in
                --version) version=$2 ;;
                --directory) directory=$2 ;;
                --repository) repository=$2 ;;
            esac
            shift 2 ;;
        --help|-h)
            cat <<'HELP'
Usage: bash install.sh [--version vX.Y.Z[-rc.N]] [--directory PATH] [--repository owner/repo]

  --directory PATH   Installation directory (default: /opt/code-server)
  --version TAG      Release tag (default: latest stable release)
  --repository NAME  GitHub owner/repo (default: Weeks-Consulting/code-server-aio)
  -h, --help         Show this help

sudo is optional when installing into a directory owned and writable by the
current user. Creating a new directory also requires a writable parent.
HELP
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done
if [[ ! $repository =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    echo 'Repository must be owner/repo.' >&2; exit 1
fi
if [[ -z $version ]]; then
    release_url=$(curl --fail --silent --show-error --location --retry 3 \
        --output /dev/null --write-out '%{url_effective}' \
        "https://github.com/$repository/releases/latest")
    version=${release_url##*/}
fi
if [[ ! $version =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[1-9][0-9]*)?$ ]]; then
    echo "Expected a release tag such as v4.141.0 or v4.141.0-rc.1; got: $version" >&2
    exit 1
fi

staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
base_url="https://raw.githubusercontent.com/$repository/refs/tags/$version"
compose_files=(compose.yaml compose.backup.yaml compose.backup-local.yaml)
deployment_files=("${compose_files[@]}")
for file in "${deployment_files[@]}" .env.example; do
    curl --fail --silent --show-error --location --retry 3 \
        "$base_url/$file" --output "$staging/$file"
done
# Older releases do not include the launcher.
launcher_status=$(curl --silent --show-error --location --retry 3 \
    "$base_url/start.sh" --output "$staging/start.sh" --write-out '%{http_code}')
case "$launcher_status" in
    200) deployment_files+=(start.sh) ;;
    404) printf 'Release %s does not include start.sh; use Docker Compose to launch.\n' "$version" ;;
    *) echo "Unable to download launcher (HTTP $launcher_status)." >&2; exit 1 ;;
esac
# Ship the runtime restore helper when present in the selected release.
if [[ $launcher_status == 200 ]]; then
    restore_status=$(curl --silent --show-error --location --retry 3 \
        "$base_url/build/tools/restore-home.sh" --output "$staging/restore-home.sh" --write-out '%{http_code}')
    case "$restore_status" in
        200) deployment_files+=(restore-home.sh) ;;
        404) printf 'Release %s does not include the restore helper.\n' "$version" ;;
        *) echo "Unable to download restore helper (HTTP $restore_status)." >&2; exit 1 ;;
    esac
fi
image="ghcr.io/${repository,,}:${version#v}"
sed "s|^CODE_SERVER_IMAGE=.*|CODE_SERVER_IMAGE=$image|" "$staging/.env.example" > "$staging/.env"

mkdir -p -- "$directory"
for file in "${deployment_files[@]}" .env; do
    if [[ -L $directory/$file || ( -e $directory/$file && ! -f $directory/$file ) ]]; then
        printf 'Expected a regular file: %s\n' "$directory/$file" >&2
        exit 1
    fi
done
if [[ -f $directory/.env ]]; then
    # Treat .env as data, never shell code. Existing keys (even empty ones) win.
    awk -v release="$version" '
        function key(line) {
            sub(/^[[:space:]]*/, "", line)
            sub(/^export[[:space:]]+/, "", line)
            sub(/[[:space:]]*=.*/, "", line)
            return line
        }
        FILENAME == ARGV[1] {
            if ($0 ~ /^[[:space:]]*(export[[:space:]]+)?[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=/)
                existing[key($0)] = 1
            next
        }
        /^[[:space:]]*#/ { notes = notes $0 "\n"; next }
        /^[[:space:]]*$/ { notes = notes "\n"; next }
        /^[A-Za-z_][A-Za-z0-9_]*=/ {
            name = key($0)
            if (!(name in existing)) {
                if (!added++) printf "\n# Settings added by installer for release %s\n", release
                printf "%s%s\n", notes, $0
                existing[name] = 1
            }
        }
        { notes = "" }
    ' "$directory/.env" "$staging/.env" > "$staging/env-additions"
fi
for file in "${deployment_files[@]}"; do
    if [[ -f $directory/$file ]]; then
        cp -p -- "$directory/$file" "$directory/$file.bak"
    fi
    install -m 644 -- "$staging/$file" "$directory/$file"
    printf 'Updated %s\n' "$directory/$file"
done
if [[ -f $directory/.env ]]; then
    if [[ -s $staging/env-additions ]]; then
        cat "$staging/env-additions" >> "$directory/.env"
        printf 'Added new settings and notes to %s/.env; existing values preserved.\n' "$directory"
    else
        printf 'Keeping existing %s/.env; no new settings.\n' "$directory"
    fi
    chmod 600 -- "$directory/.env"
else
    install -m 600 -- "$staging/.env" "$directory/.env"
fi
printf '\nInstalled deployment files in %s (release %s).\n' "$directory" "$version"
printf 'Edit %s/.env to configure Cloudflare Access and your tunnel before starting.\n' "$directory"
if [[ $launcher_status == 200 ]]; then
    printf 'Then run bash %s/start.sh to launch. No services have been started.\n' "$directory"
else
    printf 'Then run Docker Compose from that directory. No services have been started.\n'
fi
