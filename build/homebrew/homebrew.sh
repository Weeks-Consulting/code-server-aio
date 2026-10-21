# shellcheck shell=sh
# Login shells reset PATH; restore Homebrew's environment after that reset.
if [ -x /home/linuxbrew/.linuxbrew/bin/brew ]; then
    eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"
fi
