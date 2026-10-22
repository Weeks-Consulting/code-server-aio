FROM debian:13-slim

# Optional override; defaults to build/code-server.version (checksums must match).
ARG CODE_SERVER_VERSION=

ENV DEBIAN_FRONTEND=noninteractive \
    SHELL=/bin/bash

COPY build/code-server.version build/code-server.sha256 /tmp/
RUN version=${CODE_SERVER_VERSION:-$(cat /tmp/code-server.version)} && \
    printf '%s\n' "$version" | grep -Ex '[0-9]+\.[0-9]+\.[0-9]+' && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        docker-cli \
        docker.io \
        file \
        git \
        haproxy \
        locales \
        procps \
        python3 \
        python3-cryptography \
        sudo \
        supervisor && \
    sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen && \
    # Install code-server
    arch=$(dpkg --print-architecture) && \
    package="code-server_${version}_${arch}.deb" && \
    curl -fsSL "https://github.com/coder/code-server/releases/download/v${version}/${package}" -o "/tmp/${package}" && \
    cd /tmp && grep "  ${package}$" code-server.sha256 | sha256sum --check --strict - && \
    apt-get install -y --no-install-recommends "/tmp/${package}" && \
    rm "/tmp/${package}" /tmp/code-server.version /tmp/code-server.sha256 && \
    rm -rf /var/lib/apt/lists/*

# Bundle a verified installer; Homebrew itself is installed after /home is mounted.
COPY build/homebrew-install.version build/homebrew-install.sha256 /tmp/homebrew-install/
RUN commit=$(cat /tmp/homebrew-install/homebrew-install.version) && \
    printf '%s\n' "$commit" | grep -Ex '[a-f0-9]{40}' && \
    curl --fail --silent --show-error --location --retry 3 --connect-timeout 10 --max-time 60 \
        "https://raw.githubusercontent.com/Homebrew/install/${commit}/install.sh" \
        -o /usr/local/lib/homebrew-install.sh && \
    cd /usr/local/lib && \
    sha256sum --check --strict /tmp/homebrew-install/homebrew-install.sha256 && \
    chmod 644 /usr/local/lib/homebrew-install.sh && \
    rm -rf /tmp/homebrew-install

RUN groupadd --gid 10000 cloud-user && \
    useradd --uid 10000 --gid 10000 --create-home --shell /bin/bash --groups sudo,docker cloud-user && \
    printf 'cloud-user ALL=(ALL:ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/cloud-user && \
    chmod 0440 /etc/sudoers.d/cloud-user && \
    visudo -cf /etc/sudoers.d/cloud-user

ENV LANG=en_US.UTF-8 \
    HOME=/home/cloud-user

WORKDIR /home/cloud-user
EXPOSE 8080

COPY --chmod=755 build/runtime/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY build/runtime/supervisord.conf /etc/supervisor/supervisord.conf
COPY build/runtime/access-proxy.py /usr/local/lib/access-proxy.py
COPY build/runtime/access-403.html build/runtime/access-503.html /etc/haproxy/errors/
COPY build/runtime/homebrew.sh /etc/profile.d/homebrew.sh
COPY build/runtime/supervisor-events.sh /usr/local/lib/supervisor-events.sh
COPY --chmod=755 build/runtime/container-healthcheck.sh /usr/local/bin/container-healthcheck
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
