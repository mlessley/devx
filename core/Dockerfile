# --- Stage 1: Binary Source (uv) ---
FROM ghcr.io/astral-sh/uv:latest AS uv_bin

# --- Stage 2: Final (DevX Environment) ---
FROM debian:trixie-slim AS devx

# 1. Install uv binaries
COPY --from=uv_bin /uv /uvx /usr/bin/

# 2. Set uv environment variables
ENV UV_PYTHON_INSTALL_DIR=/opt/python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DISABLE_AUTOUPDATER=true
ENV GH_CLI_KEYRING=/usr/share/keyrings/githubcli-archive-keyring.gpg
ENV NODE_KEYRING=/usr/share/keyrings/nodesource.gpg

# 3. Install System Dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server \
    bat \
    git \
    curl \
    sudo \
    ca-certificates \
    gnupg \
    dnsutils \
    fd-find \
    file \
    fzf \
    iproute2 \
    iputils-ping \
    jq \
    lsof \
    make \
    netcat-openbsd \
    procps \
    ripgrep \
    rsync \
    telnet \
    tmux \
    tree \
    awscli \
    less \
    unzip \
    vim \
    zip \
    && rm -rf /var/lib/apt/lists/*

# 4. Install Node.js 22 LTS + JS package managers + Claude Code
ARG NODE_MAJOR=22
RUN curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
            | gpg --dearmor -o "$NODE_KEYRING" \
        && chmod 644 "$NODE_KEYRING" \
        && echo "deb [arch=$(dpkg --print-architecture) signed-by=$NODE_KEYRING] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
            | tee /etc/apt/sources.list.d/nodesource.list > /dev/null \
        && apt-get update \
        && apt-get install -y --no-install-recommends nodejs \
        && npm install -g yarn pnpm @anthropic-ai/claude-code \
        && rm -rf /var/lib/apt/lists/*

# 4a. Install GitHub CLI (gh)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
            | tee "$GH_CLI_KEYRING" > /dev/null \
        && chmod go+r "$GH_CLI_KEYRING" \
        && echo "deb [arch=$(dpkg --print-architecture) signed-by=$GH_CLI_KEYRING] https://cli.github.com/packages stable main" \
            | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
        && apt-get update \
        && apt-get install -y --no-install-recommends gh \
        && rm -rf /var/lib/apt/lists/*

# 4ab. Install Terraform CLI (verified against HashiCorp's published
# SHA256SUMS manifest before the archive is trusted). Note: GPG-signature
# verification of that manifest was deliberately left out here — HashiCorp
# has rotated its release-signing key more than once, and older pinned
# versions are signed with keys no longer served at their "current key"
# endpoint, which makes signature checks silently break on unrelated key
# rotations. The checksum manifest itself isn't affected by that rotation.
ARG TERRAFORM_VERSION=1.9.8
RUN set -eux; \
        arch="$(dpkg --print-architecture)"; \
        case "$arch" in \
            amd64) tf_arch='amd64' ;; \
            arm64) tf_arch='arm64' ;; \
            *) echo "Unsupported architecture for Terraform: $arch"; exit 1 ;; \
        esac; \
        workdir="$(mktemp -d)"; \
        tf_zip="terraform_${TERRAFORM_VERSION}_linux_${tf_arch}.zip"; \
        sums_file="terraform_${TERRAFORM_VERSION}_SHA256SUMS"; \
        base_url="https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}"; \
        curl -fsSL -o "$workdir/$tf_zip" "$base_url/$tf_zip"; \
        curl -fsSL -o "$workdir/$sums_file" "$base_url/$sums_file"; \
        (cd "$workdir" && sha256sum --ignore-missing -c "$sums_file"); \
        unzip "$workdir/$tf_zip" -d /tmp; \
        install -m 0755 /tmp/terraform /usr/local/bin/terraform; \
        rm -rf "$workdir" /tmp/terraform

# 4b. Install Docker Engine (dockerd + CLI; runs a nested daemon isolated by
# the sysbox-runc runtime — see docs/superpowers/specs/2026-07-30-devx-sandbox-hardening-design.md)
ARG DOCKER_CLI_VERSION=27.5.1
ARG DOCKER_COMPOSE_VERSION=2.33.1
# download.docker.com does not publish a checksum manifest for these static
# tarballs, so these are the SHA256 sums we computed ourselves for the pinned
# DOCKER_CLI_VERSION above — bump them together if that version changes.
ARG DOCKER_CLI_SHA256_AMD64=4f798b3ee1e0140eab5bf30b0edc4e84f4cdb53255a429dc3bbae9524845d640
ARG DOCKER_CLI_SHA256_ARM64=e6b53725a73763ab3f988c73f8772eaed429754c1a579db5ff11f21990fd1817
RUN apt-get update && apt-get install -y --no-install-recommends iptables \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
        arch="$(dpkg --print-architecture)"; \
        case "$arch" in \
            amd64) docker_arch='x86_64'; docker_sha256="$DOCKER_CLI_SHA256_AMD64" ;; \
            arm64) docker_arch='aarch64'; docker_sha256="$DOCKER_CLI_SHA256_ARM64" ;; \
            *) echo "Unsupported architecture for Docker Engine: $arch"; exit 1 ;; \
        esac; \
        curl -fsSL "https://download.docker.com/linux/static/stable/${docker_arch}/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz; \
        echo "${docker_sha256}  /tmp/docker.tgz" | sha256sum -c -; \
        tar -xzf /tmp/docker.tgz -C /tmp; \
        install -m 0755 /tmp/docker/* /usr/local/bin/; \
    mkdir -p /usr/local/lib/docker/cli-plugins; \
    curl -fsSL "https://github.com/docker/compose/releases/download/v${DOCKER_COMPOSE_VERSION}/docker-compose-linux-${docker_arch}" -o /usr/local/lib/docker/cli-plugins/docker-compose; \
    curl -fsSL "https://github.com/docker/compose/releases/download/v${DOCKER_COMPOSE_VERSION}/docker-compose-linux-${docker_arch}.sha256" -o /tmp/docker-compose.sha256; \
    sed "s/docker-compose-linux-${docker_arch}/docker-compose/" /tmp/docker-compose.sha256 > /tmp/docker-compose.sha256.fixed; \
    (cd /usr/local/lib/docker/cli-plugins && sha256sum -c /tmp/docker-compose.sha256.fixed); \
    rm -f /tmp/docker-compose.sha256 /tmp/docker-compose.sha256.fixed; \
    chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose; \
        rm -rf /tmp/docker /tmp/docker.tgz

# 5. Install Python Runtimes via uv
RUN uv python install 3.11 3.12

# 6. Optional Toolchain Installs
ARG INSTALL_JAVA=false
ARG INSTALL_DOTNET=false

# Install Java if requested
RUN if [ "$INSTALL_JAVA" = "true" ]; then \
    apt-get update && apt-get install -y --no-install-recommends \
    openjdk-25-jdk \
    maven \
    gradle \
    && rm -rf /var/lib/apt/lists/*; \
    fi

# Install .NET SDK if requested
RUN if [ "$INSTALL_DOTNET" = "true" ]; then \
    curl -O https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb && \
    dpkg -i packages-microsoft-prod.deb && \
    rm packages-microsoft-prod.deb && \
    apt-get update && apt-get install -y dotnet-sdk-8.0 && \
    rm -rf /var/lib/apt/lists/*; \
    fi

# [LEARN] We removed the pre-installed AI libraries from the base image.
# Instead, projects inside /devx/repos will use 'uv' to manage their own dependencies.
# This keeps our base image lean and our projects isolated.

# 7. Configure SSH (key-auth only)
RUN mkdir -p /var/run/sshd \
    && sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config \
    && sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config

# 8. Create devx user with home at /devx
RUN useradd -d /devx -s /bin/bash devx \
    && usermod -aG sudo devx \
    && echo "devx ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
    && groupadd -f docker \
    && usermod -aG docker devx \
    && git config --global init.defaultBranch main

# 9. Install Orchestration Tools
ARG DEVX_VERSION=unknown
ARG DEVX_ORIGIN=unknown
RUN echo $DEVX_VERSION > /usr/local/etc/devx_version \
    && echo $DEVX_ORIGIN > /usr/local/etc/devx_origin

RUN touch /var/log/devx_adhoc_history && chmod 666 /var/log/devx_adhoc_history
COPY repo_watcher.py /usr/local/bin/repo_watcher.py
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/repo_watcher.py /usr/local/bin/entrypoint.sh

RUN mkdir -p /devx
EXPOSE 22

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/sbin/sshd", "-D"]
