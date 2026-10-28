FROM debian:13-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad

ENV DEBIAN_FRONTEND=noninteractive \
    SHELL=/bin/bash

COPY build/versions.json /tmp/versions.json
COPY build/tools/fetch-pinned.py /tmp/fetch-pinned.py
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        docker-cli \
        docker.io \
        file \
        git \
        locales \
        procps \
        python3 \
        sudo \
        supervisor && \
    sed -i 's/# en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen && \
    locale-gen && \
    # Install the verified code-server package.
    python3 /tmp/fetch-pinned.py /tmp/versions.json code-server "$(dpkg --print-architecture)" /tmp/code-server.deb && \
    apt-get install -y --no-install-recommends /tmp/code-server.deb && \
    rm /tmp/code-server.deb && \
    rm -rf /var/lib/apt/lists/*

# Install the pinned static Envoy executable for this image's architecture.
RUN python3 /tmp/fetch-pinned.py /tmp/versions.json envoy "$(dpkg --print-architecture)" /tmp/envoy && \
    install -m 755 /tmp/envoy /usr/local/bin/envoy && \
    rm /tmp/envoy /tmp/versions.json /tmp/fetch-pinned.py

RUN groupadd --gid 10000 cloud-user && \
    useradd --uid 10000 --gid 10000 --create-home --shell /bin/bash --groups sudo,docker cloud-user && \
    printf 'cloud-user ALL=(ALL:ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/cloud-user && \
    chmod 0440 /etc/sudoers.d/cloud-user && \
    visudo -cf /etc/sudoers.d/cloud-user

RUN useradd --system --gid 10000 --no-create-home --shell /usr/sbin/nologin envoy && \
    install -d -o envoy -g 10000 -m 750 /run/envoy
COPY build/runtime/envoy.json.template /etc/envoy/envoy.json.template
COPY build/runtime/start-envoy.py /usr/local/lib/start-envoy.py

ENV LANG=en_US.UTF-8 \
    HOME=/home/cloud-user

WORKDIR /home/cloud-user

COPY --chmod=755 build/runtime/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY build/runtime/start-code-server.sh /usr/local/lib/start-code-server.sh
COPY build/runtime/supervisord.conf /etc/supervisor/supervisord.conf
COPY build/runtime/supervisor-events.py /usr/local/lib/supervisor-events.py
COPY --chmod=755 build/runtime/container-healthcheck.sh /usr/local/bin/container-healthcheck
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
