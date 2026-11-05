FROM debian:13-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 AS download-tools

# Only used to fetch checksum-pinned artifacts; never copied into the runtime.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates curl python3 && \
    rm -rf /var/lib/apt/lists/*

FROM download-tools AS code-server-download
# Defaults are synchronized from build/versions.json by just sync-pins.
ARG CODE_SERVER_VERSION=4.145.0
ARG CODE_SERVER_SHA256_AMD64=43fa1dc0e7929753bb3442e4ef40d4beb8bb5f8fe63601ede31b305536e6275b
ARG CODE_SERVER_SHA256_ARM64=43813b81df484b49facf19d99faaa51683a2cdd8c652dc97ebddefa9cbe7fd6a
RUN --mount=type=bind,source=build/tools/fetch-pinned.py,target=/tmp/fetch-pinned.py \
    arch=$(dpkg --print-architecture) && \
    case "$arch" in \
        amd64) checksum="$CODE_SERVER_SHA256_AMD64" ;; \
        arm64) checksum="$CODE_SERVER_SHA256_ARM64" ;; \
        *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac && \
    python3 /tmp/fetch-pinned.py code-server "$CODE_SERVER_VERSION" "$arch" "$checksum" /tmp/code-server.deb

FROM download-tools AS envoy-download
# Defaults are synchronized from build/versions.json by just sync-pins.
ARG ENVOY_VERSION=1.40.2
ARG ENVOY_SHA256_AMD64=1d64506e25c1844f146162ddb2ece7010fa4e784faddb40a079021491f1f03cc
ARG ENVOY_SHA256_ARM64=b9d6d1e06dd5838a3506de9f4deebad3de9a2ce6cadb87cd76222ca286213baf
RUN --mount=type=bind,source=build/tools/fetch-pinned.py,target=/tmp/fetch-pinned.py \
    arch=$(dpkg --print-architecture) && \
    case "$arch" in \
        amd64) checksum="$ENVOY_SHA256_AMD64" ;; \
        arm64) checksum="$ENVOY_SHA256_ARM64" ;; \
        *) echo "Unsupported architecture: $arch" >&2; exit 1 ;; \
    esac && \
    python3 /tmp/fetch-pinned.py envoy "$ENVOY_VERSION" "$arch" "$checksum" /tmp/envoy

FROM debian:13-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2 AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    SHELL=/bin/bash

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
    rm -rf /var/lib/apt/lists/*

# Mount verified downloads so temporary artifacts do not occupy image layers.
RUN --mount=type=bind,from=code-server-download,source=/tmp/code-server.deb,target=/tmp/code-server.deb \
    --mount=type=bind,from=envoy-download,source=/tmp/envoy,target=/tmp/envoy \
    apt-get update && \
    apt-get install -y --no-install-recommends /tmp/code-server.deb && \
    install -m 755 /tmp/envoy /usr/local/bin/envoy && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10000 cloud-user && \
    useradd --uid 10000 --gid 10000 --create-home --shell /bin/bash --groups sudo,docker cloud-user && \
    printf 'cloud-user ALL=(ALL:ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/cloud-user && \
    chmod 0440 /etc/sudoers.d/cloud-user && \
    visudo -cf /etc/sudoers.d/cloud-user

RUN useradd --system --gid 10000 --no-create-home --shell /usr/sbin/nologin envoy && \
    install -d -o envoy -g 10000 -m 750 /run/envoy
COPY build/runtime/envoy/ /etc/envoy/
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
