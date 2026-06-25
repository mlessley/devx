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

# 3. Install System Dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssh-server \
    git \
    curl \
    sudo \
    ca-certificates \
    ripgrep \
    vim \
    && rm -rf /var/lib/apt/lists/*

# 4. Install Node.js 22 LTS + JS package managers + Claude Code
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g yarn pnpm @anthropic-ai/claude-code \
    && rm -rf /var/lib/apt/lists/*

# 5. Install Python Runtimes via uv
RUN uv python install 3.11 3.12

# 6. Optional Toolchain Installs
ARG INSTALL_JAVA=false
ARG INSTALL_DOTNET=false

# Install Java if requested
RUN if [ "$INSTALL_JAVA" = "true" ]; then \
    apt-get update && apt-get install -y --no-install-recommends \
    openjdk-11-jdk \
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

# 7. Configure SSH (Key-auth + Password fallback)
RUN mkdir -p /var/run/sshd \
    && sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config

# 8. Create devx user with home at /devx
RUN useradd -d /devx -s /bin/bash devx \
    && echo "root:devx" | chpasswd \
    && echo "devx:devx" | chpasswd \
    && usermod -aG sudo devx \
    && echo "devx ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
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
