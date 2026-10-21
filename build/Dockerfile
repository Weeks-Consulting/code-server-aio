FROM debian:13-slim

ARG CODE_SERVER_VERSION

ENV DEBIAN_FRONTEND=noninteractive \
    SHELL=/bin/bash

# Package lists are grouped by purpose under build/dependencies/.
COPY build/dependencies/ /tmp/dependencies/
RUN test -n "$CODE_SERVER_VERSION" && \
    printf '%s\n' "$CODE_SERVER_VERSION" | grep -Ex '[0-9]+\.[0-9]+\.[0-9]+' && \
    apt-get update && \
    cat /tmp/dependencies/*.txt > /tmp/packages.txt && \
    xargs -r -a /tmp/packages.txt apt-get install -y --no-install-recommends && \
    sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen && \
    # Install code-server
    curl -fsSL https://code-server.dev/install.sh -o /tmp/install-code-server.sh && \
    sh /tmp/install-code-server.sh --version "${CODE_SERVER_VERSION}" && \
    rm /tmp/install-code-server.sh && \
    rm -rf /var/lib/apt/lists/* /tmp/dependencies /tmp/packages.txt

RUN groupadd --gid 10000 cloud-user && \
    useradd --uid 10000 --gid 10000 --create-home --shell /bin/bash --groups sudo,docker cloud-user && \
    printf 'cloud-user ALL=(ALL:ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/cloud-user && \
    chmod 0440 /etc/sudoers.d/cloud-user && \
    visudo -cf /etc/sudoers.d/cloud-user && \
    mkdir -p /workspace && \
    chown cloud-user:cloud-user /workspace

ENV LANG=en_US.UTF-8 \
    HOME=/home/cloud-user \
    PATH="/home/linuxbrew/.linuxbrew/bin:/home/linuxbrew/.linuxbrew/sbin:${PATH}"

WORKDIR /home/cloud-user
EXPOSE 8080

COPY --chmod=755 build/startup/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY build/supervisor/supervisord.conf /etc/supervisor/supervisord.conf
COPY build/proxy/access-proxy.py /usr/local/lib/access-proxy.py
COPY build/proxy/errors/ /etc/haproxy/errors/
COPY build/homebrew/homebrew.sh /etc/profile.d/homebrew.sh
COPY build/supervisor/supervisor-events.py /usr/local/lib/supervisor-events.py
COPY --chmod=755 build/health/container-healthcheck.py /usr/local/bin/container-healthcheck
USER root
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
