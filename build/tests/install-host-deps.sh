#!/bin/sh
# Bootstrap only the tools needed by the shared host checks in CI.
set -eu

case "$(uname -s)" in
    Linux)
        apt-get update
        apt-get install -y --no-install-recommends bash python3 shellcheck docker-cli docker-compose
        ;;
    Darwin)
        brew install python shellcheck docker docker-compose
        # Isolate CLI plugin registration from the runner's existing Docker config.
        plugin_config="$RUNNER_TEMP/docker-config"
        mkdir -p "$plugin_config/cli-plugins"
        ln -s "$(brew --prefix)/bin/docker-compose" "$plugin_config/cli-plugins/docker-compose"
        echo "DOCKER_CONFIG=$plugin_config" >> "$GITHUB_ENV"
        ;;
    *) echo 'Host checks support Debian Linux and macOS.' >&2; exit 1 ;;
esac
