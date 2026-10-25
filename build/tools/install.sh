#!/bin/sh
# Install or refresh a published Compose deployment without building the project.
set -eu

repository=Weeks-Consulting/code-server-aio
directory=/opt/code-server
version=
image_tag=latest
dry_run=false
while [ "$#" -gt 0 ]; do
    case "$1" in
        --version|--directory|--repository)
            if [ "$#" -lt 2 ] || [ -z "$2" ] || [ "${2#--}" != "$2" ]; then echo "Missing value for $1" >&2; exit 1; fi
            case "$1" in
                --version) version=$2; image_tag=${2#v} ;;
                --directory) directory=$2 ;;
                --repository) repository=$2 ;;
            esac
            shift 2 ;;
        --dry-run) dry_run=true; shift ;;
        --help|-h)
            cat <<'HELP'
Usage: sh install.sh [--version vX.Y.Z[-N]] [--directory PATH] [--repository owner/repo] [--dry-run]

  --directory PATH   Installation directory (default: /opt/code-server)
  --version TAG      Release tag and image version (default image: latest)
  --repository NAME  GitHub owner/repo (default: Weeks-Consulting/code-server-aio)
  --dry-run          Show dependency and deployment changes without making them
  -h, --help         Show this help

sudo is optional when installing into a directory owned and writable by the
current user. Creating a new directory also requires a writable parent.
Docker must already be installed. Missing runtime tools are installed using
apt (with sudo when needed) on Debian or Homebrew on macOS. Run without sudo on
macOS. Dry runs make no package, network, or deployment changes.
HELP
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done
if printf '%s\n' "$repository" | LC_ALL=C grep -Eqv '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'; then
    echo 'Repository must be owner/repo.' >&2; exit 1
fi
# Reject invalid requests before installing packages or making network requests.
if [ -e "$directory" ] && [ ! -d "$directory" ]; then
    printf 'Expected a deployment directory: %s\n' "$directory" >&2; exit 1
fi
if [ -n "$version" ] && printf '%s\n' "$version" | LC_ALL=C grep -Eqv '^v[0-9]+\.[0-9]+\.[0-9]+(-[1-9][0-9]*)?$'; then
    echo "Expected a release tag such as v4.141.0 or v4.141.0-1; got: $version" >&2; exit 1
fi
case "$directory" in /*) ;; *) directory="$PWD/$directory" ;; esac
# Root must not write through paths controlled by another user, including ancestors.
if [ "$(id -u)" -eq 0 ]; then
    candidate=$directory
    while :; do
        if [ -e "$candidate" ] || [ -L "$candidate" ]; then
            if [ -L "$candidate" ] || [ "$(find "$candidate" -maxdepth 0 -user root ! -perm -020 ! -perm -002 -print)" != "$candidate" ]; then
                echo "Root installation requires root-owned directories without group/world write access: $candidate" >&2
                exit 1
            fi
        fi
        parent=$(dirname -- "$candidate")
        [ "$parent" != "$candidate" ] || break
        candidate=$parent
    done
fi
for file in compose.yaml compose.backup.yaml compose.backup-local.yaml manage restore-home.sh backup-key.sh .env; do
    if [ -L "$directory/$file" ] || { [ -e "$directory/$file" ] && [ ! -f "$directory/$file" ]; }; then
        printf 'Expected a regular file: %s\n' "$directory/$file" >&2; exit 1
    fi
done

# Docker's engine is supplied by the host; the installer supplies runtime tools.
command -v docker >/dev/null || {
    echo 'A working Docker installation is required before running this installer.' >&2
    exit 1
}
run_dependency_command() {
    if $dry_run; then
        printf 'Would run:'
        for argument do printf ' %s' "$argument"; done
        printf '\n'
    else
        "$@"
    fi
}
set --
case "$(uname -s)" in
    Darwin)
        command -v curl >/dev/null || set -- "$@" curl
        command -v python3 >/dev/null || set -- "$@" python
        command -v gpg >/dev/null || set -- "$@" gnupg
        compose_missing=false
        if ! docker compose version >/dev/null 2>&1; then
            set -- "$@" docker-compose
            compose_missing=true
        fi
        if [ "$#" -gt 0 ]; then
            command -v brew >/dev/null || {
                echo 'Install Homebrew to install the missing runtime dependencies.' >&2; exit 1;
            }
            if [ "$(id -u)" -eq 0 ]; then
                echo 'Run the installer without sudo on macOS so Homebrew can install dependencies; choose a directory in your home folder.' >&2
                exit 1
            fi
            run_dependency_command brew install "$@"
        fi
        if $compose_missing; then
            plugin_dir="${DOCKER_CONFIG:-$HOME/.docker}/cli-plugins"
            if $dry_run; then
                printf 'Would register the Homebrew Compose plugin in %s if Docker cannot discover it.\n' "$plugin_dir"
            elif ! docker compose version >/dev/null 2>&1; then
                mkdir -p "$plugin_dir"
                target="$plugin_dir/docker-compose"
                if [ -e "$target" ] || [ -L "$target" ]; then
                    printf 'Docker cannot load existing plugin %s; repair it and rerun the installer.\n' "$target" >&2
                    exit 1
                fi
                brew_prefix=$(brew --prefix)
                ln -s "$brew_prefix/lib/docker/cli-plugins/docker-compose" "$target"
            fi
        fi
        ;;
    Linux)
        if [ ! -f /etc/debian_version ]; then
            echo 'The installer supports Debian Linux and macOS.' >&2; exit 1
        fi
        command -v curl >/dev/null || set -- "$@" curl
        command -v python3 >/dev/null || set -- "$@" python3
        command -v gpg >/dev/null || set -- "$@" gnupg
        if ! dpkg-query -W -f='${Status}' ca-certificates 2>/dev/null | grep -qx 'install ok installed'; then
            set -- "$@" ca-certificates
        fi
        if ! docker compose version >/dev/null 2>&1; then
            if dpkg-query -W -f='${Status}' docker-ce-cli 2>/dev/null | grep -qx 'install ok installed'; then
                set -- "$@" docker-compose-plugin
            else
                set -- "$@" docker-compose
            fi
        fi
        if [ "$#" -gt 0 ]; then
            if [ "$(id -u)" -eq 0 ]; then
                run_dependency_command apt-get update
                run_dependency_command apt-get install -y "$@"
            else
                command -v sudo >/dev/null || { echo 'Install missing runtime dependencies as root or install sudo.' >&2; exit 1; }
                run_dependency_command sudo apt-get update
                run_dependency_command sudo apt-get install -y "$@"
            fi
        fi
        ;;
    *) echo 'The installer supports Debian Linux and macOS.' >&2; exit 1 ;;
esac

if $dry_run; then
    if [ -z "$version" ]; then
        printf 'Would resolve the latest stable release from https://github.com/%s/releases/latest.\n' "$repository"
    else
        printf 'Would use release %s from %s.\n' "$version" "$repository"
    fi
    if [ ! -d "$directory" ]; then printf 'Would create deployment directory: %s\n' "$directory"; fi
    for file in compose.yaml compose.backup.yaml compose.backup-local.yaml manage restore-home.sh backup-key.sh; do
        if [ -f "$directory/$file" ]; then
            printf 'Would save %s/%s as %s.bak before replacing it.\n' "$directory" "$file" "$file"
        fi
        printf 'Would download and install %s/%s (helpers only when included in the release).\n' "$directory" "$file"
    done
    if [ -f "$directory/.env" ]; then
        printf 'Would preserve existing %s/.env values and append missing release settings.\n' "$directory"
    else
        printf 'Would create %s/.env from the release template with its image selected.\n' "$directory"
    fi
    printf 'Would set CODE_SERVER_IMAGE=ghcr.io/%s:%s in .env unless an existing digest pin is present.\n' "$(printf '%s' "$repository" | tr '[:upper:]' '[:lower:]')" "$image_tag"
    printf 'Would set .env mode to 600 and generate a backup key only if missing or empty.\n'
    printf 'Dry run complete. No packages installed, releases downloaded, files changed, or services started.\n'
    exit 0
fi
command -v curl >/dev/null
command -v python3 >/dev/null
command -v gpg >/dev/null
docker compose version >/dev/null

if [ -z "$version" ]; then
    release_url=$(curl --fail --silent --show-error --location --retry 3 \
        --output /dev/null --write-out '%{url_effective}' \
        "https://github.com/$repository/releases/latest")
    version=${release_url##*/}
fi
if printf '%s\n' "$version" | LC_ALL=C grep -Eqv '^v[0-9]+\.[0-9]+\.[0-9]+(-[1-9][0-9]*)?$'; then
    echo "Expected a release tag such as v4.141.0 or v4.141.0-1; got: $version" >&2
    exit 1
fi

staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
base_url="https://raw.githubusercontent.com/$repository/refs/tags/$version"
deployment_files="compose.yaml compose.backup.yaml compose.backup-local.yaml"
for file in $deployment_files .env.example; do
    curl --fail --silent --show-error --location --retry 3 \
        "$base_url/$file" --output "$staging/$file"
done
# Encrypted-backup releases ship a shared key initializer for install and startup.
if grep -q '^GPG_PASSPHRASE=' "$staging/.env.example"; then
    curl --fail --silent --show-error --location --retry 3 \
        "$base_url/build/tools/backup-key.sh" --output "$staging/backup-key.sh"
    deployment_files="$deployment_files backup-key.sh"
fi
# Older releases do not include the launcher.
launcher_status=$(curl --silent --show-error --location --retry 3 \
    "$base_url/manage" --output "$staging/manage" --write-out '%{http_code}')
case "$launcher_status" in
    200) deployment_files="$deployment_files manage" ;;
    404) printf 'Release %s does not include manage; use Docker Compose to launch.\n' "$version" ;;
    *) echo "Unable to download launcher (HTTP $launcher_status)." >&2; exit 1 ;;
esac
# Ship the runtime restore helper when present in the selected release.
if [ "$launcher_status" = 200 ]; then
    restore_status=$(curl --silent --show-error --location --retry 3 \
        "$base_url/build/tools/restore-home.sh" --output "$staging/restore-home.sh" --write-out '%{http_code}')
    case "$restore_status" in
        200) deployment_files="$deployment_files restore-home.sh" ;;
        404) printf 'Release %s does not include the restore helper.\n' "$version" ;;
        *) echo "Unable to download restore helper (HTTP $restore_status)." >&2; exit 1 ;;
    esac
fi
# New releases attach checksums; older releases remain installable with a warning.
checksums_status=$(curl --silent --show-error --location --retry 3 \
    "https://github.com/$repository/releases/download/$version/SHA256SUMS" \
    --output "$staging/SHA256SUMS" --write-out '%{http_code}')
case "$checksums_status" in
    200)
        # The file list is constructed internally and intentionally split.
        # shellcheck disable=SC2086
        python3 - "$staging" $deployment_files .env.example <<'PYVERIFY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
checksums = {}
for line in (root / "SHA256SUMS").read_text().splitlines():
    digest, name = line.split(maxsplit=1)
    checksums[name] = digest
for name in sys.argv[2:]:
    source = "build/tools/" + name if name in ("restore-home.sh", "backup-key.sh") else name
    if checksums.get(source) != hashlib.sha256((root / name).read_bytes()).hexdigest():
        sys.exit("Release checksum missing or incorrect: " + name)
PYVERIFY
        ;;
    404) echo 'Warning: this older release has no SHA256SUMS; downloads cannot be checksum-verified.' >&2 ;;
    *) echo "Unable to download release checksums (HTTP $checksums_status)." >&2; exit 1 ;;
esac
image_repository=$(printf '%s' "$repository" | tr '[:upper:]' '[:lower:]')
image="ghcr.io/$image_repository:$image_tag"
sed "s|^CODE_SERVER_IMAGE=.*|CODE_SERVER_IMAGE=$image|" "$staging/.env.example" > "$staging/.env"

mkdir -p -- "$directory"
for file in $deployment_files .env; do
    if [ -L "$directory/$file" ] || { [ -e "$directory/$file" ] && [ ! -f "$directory/$file" ]; }; then
        printf 'Expected a regular file: %s\n' "$directory/$file" >&2
        exit 1
    fi
done
if [ -f "$directory/.env" ]; then
    # Treat .env as data, never shell code. Preserve settings except the selected image.
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
for file in $deployment_files; do
    if [ -f "$directory/$file" ]; then
        if [ -d "$directory/$file.bak" ]; then
            printf 'Backup destination must not be a directory: %s.bak\n' "$directory/$file" >&2
            exit 1
        fi
        mv -f -- "$directory/$file" "$directory/$file.bak"
    fi
    mode=644
    case "$file" in manage|*.sh) mode=755 ;; esac
    install -m "$mode" -- "$staging/$file" "$directory/$file"
    printf 'Updated %s\n' "$directory/$file"
done
if [ -f "$directory/.env" ]; then
    if [ -s "$staging/env-additions" ]; then
        cat "$staging/env-additions" >> "$directory/.env"
        printf 'Added new settings and notes to %s/.env; other existing values preserved.\n' "$directory"
    else
        printf 'Keeping existing %s/.env; no new settings.\n' "$directory"
    fi
    chmod 600 "$directory/.env"
else
    install -m 600 -- "$staging/.env" "$directory/.env"
fi
# Preserve deliberate digest pins; update mutable tags without executing dotenv content.
python3 - "$directory/.env" "$image" <<'PYIMAGE'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
lines = path.read_text().splitlines(keepends=True)
setting = "CODE_SERVER_IMAGE=" + sys.argv[2] + "\n"
found = False
# The last assignment is the effective dotenv value. Keep all image lines intact
# when it selects a digest, including quoting and comments.
values = [match.group(1) for line in lines
          if (match := re.match(r"\s*(?:export\s+)?CODE_SERVER_IMAGE\s*=(.*)", line))]
if values and re.search(r"@sha256:[a-fA-F0-9]{64}(?=[\s\"'#]|$)", values[-1]):
    print("Warning: keeping existing CODE_SERVER_IMAGE digest pin; "
          "verify a new digest or run manage upgrade to deliberately replace it.",
          file=sys.stderr)
    sys.exit(0)
for index, line in enumerate(lines):
    if re.match(r"\s*(?:export\s+)?CODE_SERVER_IMAGE\s*=", line):
        lines[index] = setting
        found = True
if not found:
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(setting)
path.write_text("".join(lines))
PYIMAGE
if [ -f "$staging/backup-key.sh" ]; then
    # Honor the helper's shebang, including Bash helpers in older Linux releases.
    "$directory/backup-key.sh" "$directory/.env"
fi
printf '\nInstalled deployment files in %s (release %s).\n' "$directory" "$version"
printf 'Edit %s/.env to configure Cloudflare Access and your tunnel before starting.\n' "$directory"
if [ "$launcher_status" = 200 ]; then
    if [ "$(id -u)" -eq 0 ]; then
        printf 'Then run sudo sh "%s/manage" to launch. No services have been started.\n' "$directory"
    else
        printf 'Then run sh "%s/manage" as the same user that installed it to launch. No services have been started.\n' "$directory"
    fi
else
    printf 'Then run Docker Compose from that directory. No services have been started.\n'
fi
