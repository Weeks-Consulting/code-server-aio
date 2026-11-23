#!/bin/sh
# Run as cloud-user in a disposable workspace with the default Linuxbrew prefix.
set -eu

prefix=/home/linuxbrew/.linuxbrew
brew_tag=7.0.13
brew_commit=bc63375ac3a4d6debb1ceb441ae6e22854f0909a
sudo -n install -d -o cloud-user -g cloud-user "$prefix"
mkdir -p "$prefix/bin" "$prefix/Homebrew"
git -C "$prefix/Homebrew" init --quiet
git -C "$prefix/Homebrew" remote add origin https://github.com/Homebrew/brew.git
git -C "$prefix/Homebrew" fetch --depth=1 origin "$brew_commit"
git -C "$prefix/Homebrew" tag "$brew_tag" "$brew_commit"
git -C "$prefix/Homebrew" checkout --quiet --detach "$brew_commit"
ln -s ../Homebrew/bin/brew "$prefix/bin/brew"

# Homebrew checks for a core tap in source mode. This fixture has no core dependencies.
core_tap="$prefix/Homebrew/Library/Taps/homebrew/homebrew-core"
git init --quiet --initial-branch=master "$core_tap"
git -C "$core_tap" -c user.name=Workspace -c user.email=workspace@example.invalid \
    commit --allow-empty -qm 'Empty core tap for the smoke test'

fixture=$(mktemp -d)
trap 'rm -rf "$fixture"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -p "$fixture/source" "$fixture/tap/Formula"
printf 'ready\n' > "$fixture/source/ready"
tar -czf "$fixture/workspace-smoke-1.0.tar.gz" -C "$fixture/source" ready
checksum=$(sha256sum "$fixture/workspace-smoke-1.0.tar.gz")
cat > "$fixture/tap/Formula/workspace-smoke.rb" <<EOF
class WorkspaceSmoke < Formula
  desc "Disposable service for the workspace smoke test"
  homepage "https://github.com/Weeks-Consulting/code-server-aio"
  url "file://$fixture/workspace-smoke-1.0.tar.gz"
  sha256 "${checksum%% *}"

  def install
    bin.install "ready"
  end

  service do
    run ["/usr/bin/python3", "-I", "-m", "http.server", "11211", "--bind", "127.0.0.1",
         "--directory", opt_bin]
  end
end
EOF
git -C "$fixture/tap" init --quiet
git -C "$fixture/tap" add Formula
git -C "$fixture/tap" -c user.name=Workspace -c user.email=workspace@example.invalid commit -qm 'Add smoke test formula'
brew tap workspace/smoke "$fixture/tap"
brew install workspace/smoke/workspace-smoke
