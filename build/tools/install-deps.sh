#!/bin/sh
# Install development dependencies for a host with Docker already configured.
set -eu

case "$(uname -s)" in
    Linux|Darwin) ;;
    *) echo 'just install supports Linux and macOS.' >&2; exit 1 ;;
esac

# Report every missing tool before exiting when no package manager is available.
set --
for tool in docker python3 gpg shellcheck curl act; do
    if ! command -v "$tool" >/dev/null; then set -- "$@" "$tool"; fi
done
for plugin in compose buildx; do
    if ! docker "$plugin" version >/dev/null 2>&1; then set -- "$@" "docker $plugin"; fi
done
if ! command -v brew >/dev/null; then
    if [ "$#" -ne 0 ]; then
        printf 'Homebrew is unavailable. Missing dependencies:\n' >&2
        printf '  %s\n' "$@" >&2
        exit 1
    fi
    echo 'All build and test dependencies are available.'
    exit 0
fi

command -v docker >/dev/null || {
    echo 'Install Docker first, then rerun just install.' >&2
    exit 1
}

set -- python gnupg shellcheck curl act
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

docker compose version
docker buildx version
echo 'Build and test dependencies installed. Run just test to verify the project.'
