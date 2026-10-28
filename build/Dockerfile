FROM debian:13-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad

ENV DEBIAN_FRONTEND=noninteractive \
    SHELL=/bin/bash

COPY build/versions.json /tmp/versions.json
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
    # Install code-server
    version=$(python3 -c 'import json; print(json.load(open("/tmp/versions.json"))["packages"]["code-server"]["version"])') && \
    arch=$(dpkg --print-architecture) && \
    package="code-server_${version}_${arch}.deb" && \
    curl -fsSL "https://github.com/coder/code-server/releases/download/v${version}/${package}" -o "/tmp/${package}" && \
    python3 -c 'import json,sys; p=json.load(open("/tmp/versions.json"))["packages"]["code-server"]; print(p["sha256"][sys.argv[1]] + "  /tmp/" + sys.argv[1])' "$package" | sha256sum --check --strict - && \
    apt-get install -y --no-install-recommends "/tmp/${package}" && \
    rm "/tmp/${package}" && \
    rm -rf /var/lib/apt/lists/*

# Install the pinned static Envoy executable for this image's architecture.
RUN mkdir /tmp/envoy && \
    version=$(python3 -c 'import json; print(json.load(open("/tmp/versions.json"))["packages"]["envoy"]["version"])') && \
    printf '%s\n' "$version" | grep -Ex '[0-9]+\.[0-9]+\.[0-9]+' && \
    case "$(dpkg --print-architecture)" in amd64) arch=x86_64 ;; arm64) arch=aarch_64 ;; *) exit 1 ;; esac && \
    binary="envoy-${version}-linux-${arch}" && \
    curl --fail --silent --show-error --location --retry 3 --connect-timeout 10 --max-time 300 \
        "https://github.com/envoyproxy/envoy/releases/download/v${version}/${binary}" -o "/tmp/envoy/${binary}" && \
    cd /tmp/envoy && python3 -c 'import json,sys; p=json.load(open("/tmp/versions.json"))["packages"]["envoy"]; print(p["sha256"][sys.argv[1]] + "  " + sys.argv[1])' "$binary" | sha256sum --check --strict - && \
    install -m 755 "$binary" /usr/local/bin/envoy && \
    rm -rf /tmp/envoy /tmp/versions.json

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
COPY build/runtime/initialize-workspace.sh /usr/local/lib/initialize-workspace.sh
COPY build/runtime/start-code-server.sh /usr/local/lib/start-code-server.sh
COPY build/runtime/supervisord.conf /etc/supervisor/supervisord.conf
COPY build/runtime/supervisor-events.py /usr/local/lib/supervisor-events.py
COPY --chmod=755 build/runtime/container-healthcheck.sh /usr/local/bin/container-healthcheck
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
