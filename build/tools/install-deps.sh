#!/bin/sh
# Install development dependencies for a host with Docker already configured.
set -eu

command -v docker >/dev/null || {
    echo 'Install Docker first, then rerun just install.' >&2
    exit 1
}

case "$(uname -s)" in
    Darwin)
        command -v brew >/dev/null || {
            echo 'Install Homebrew first, then rerun just install.' >&2
            exit 1
        }
        set -- python gnupg shellcheck curl
        if ! docker compose version >/dev/null 2>&1; then set -- "$@" docker-compose; fi
        if ! docker buildx version >/dev/null 2>&1; then set -- "$@" docker-buildx; fi
        brew install "$@"

        # Homebrew plugins are not always in Docker's default search path.
        # Register missing plugins without replacing existing Docker configuration.
        plugin_dir="${DOCKER_CONFIG:-$HOME/.docker}/cli-plugins"
        brew_prefix=$(brew --prefix)
        for plugin in compose buildx; do
            if ! docker "$plugin" version >/dev/null 2>&1; then
                mkdir -p "$plugin_dir"
                target="$plugin_dir/docker-$plugin"
                if [ -e "$target" ] || [ -L "$target" ]; then
                    printf 'Docker cannot load existing plugin %s; repair it and rerun just install.\n' "$target" >&2
                    exit 1
                fi
                ln -s "$brew_prefix/lib/docker/cli-plugins/docker-$plugin" "$target"
            fi
        done
        ;;
    Linux)
        if [ ! -f /etc/debian_version ]; then
            echo 'just install supports Debian Linux and macOS with Homebrew.' >&2
            exit 1
        fi
        apt_get() {
            if [ "$(id -u)" -eq 0 ]; then apt-get "$@"; else sudo apt-get "$@"; fi
        }
        set -- python3 gnupg shellcheck curl ca-certificates
        # Docker CE and Debian's Docker packages use different plugin names.
        if dpkg-query -W -f='${Status}' docker-ce-cli 2>/dev/null | grep -qx 'install ok installed'; then
            compose_package=docker-compose-plugin
            buildx_package=docker-buildx-plugin
        else
            compose_package=docker-compose
            buildx_package=docker-buildx
        fi
        if ! docker compose version >/dev/null 2>&1; then set -- "$@" "$compose_package"; fi
        if ! docker buildx version >/dev/null 2>&1; then set -- "$@" "$buildx_package"; fi
        apt_get update
        apt_get install -y "$@"
        ;;
    *)
        echo 'just install supports Debian Linux and macOS with Homebrew.' >&2
        exit 1
        ;;
esac

docker compose version
docker buildx version
echo 'Build and test dependencies installed. Run just test to verify the project.'
