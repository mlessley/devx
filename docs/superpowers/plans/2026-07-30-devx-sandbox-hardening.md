# DevX Sandbox Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the sandbox's Docker-outside-of-Docker (host socket mount) with a sysbox-runc-isolated nested Docker Engine, so the agent keeps its build/run/compose workflow without any path to host root.

**Architecture:** The `sandbox` service in `core/docker-compose.yml` runs under the `sysbox-runc` OCI runtime instead of default `runc`. `core/Dockerfile` installs a full Docker Engine (not just the CLI) so `core/entrypoint.sh` can start a real, ordinary `dockerd` inside the container. Root inside the sandbox — including via `sudo` — maps to an unprivileged UID on the host because of sysbox's user-namespace isolation, so the previous DooD escape path (`docker.sock` + passwordless sudo = host root) no longer exists. Ad hoc access to ports the agent's nested containers publish goes through SSH tunnels, not new host port mappings.

**Tech Stack:** Docker Engine (static binaries), sysbox-ce v0.7.0, Docker Compose, bash, Python 3 (devx CLI), OpenSSH.

## Global Constraints

- Sysbox version pinned: `0.7.0` (verified current release, assets `sysbox-ce_0.7.0.linux_{amd64,arm64}.deb` at `https://github.com/nestybox/sysbox/releases/download/v0.7.0/`).
- Host verified compatible: WSL2 Ubuntu 24.04.4, kernel `6.6.87.2-microsoft-standard-WSL2` (exceeds sysbox's kernel ≥6.3 threshold, so no shiftfs needed), Docker Engine `29.6.1` installed natively via `docker-ce` apt package (not snap), systemd is PID 1.
- No `--privileged`, ever, on the sandbox service.
- No manual `cap_drop`/`security-opt`/seccomp overrides on the sandbox service — sysbox manages isolation itself; hand-rolled capability restrictions risk breaking nested Docker.
- `devx`'s `NOPASSWD:ALL` sudo is retained intentionally (see spec's Rationale section) — do not remove it as part of this work.
- Any command that installs software on the host, restarts the host's `dockerd`, or removes existing Docker containers/images on the host is a **manual, confirm-before-running step** — never execute it unattended. This applies to Task 1 and Task 2 below.
- Spec: `docs/superpowers/specs/2026-07-30-devx-sandbox-hardening-design.md`

---

### Task 1: Host sysbox install script

**Files:**
- Create: `scripts/install-sysbox.sh`

**Interfaces:**
- Produces: a `sysbox` systemd service running on the host, and `sysbox-runc` registered as a Docker runtime (verifiable via `docker info --format '{{json .Runtimes}}'`). Task 2 and all later tasks that run containers under `runtime: sysbox-runc` depend on this.

- [ ] **Step 1: Write the install script**

```bash
#!/usr/bin/env bash
# Installs the sysbox-ce container runtime on the Docker host and registers
# it with the host's Docker Engine, so containers can opt into
# `runtime: sysbox-runc` for unprivileged Docker-in-Docker.
#
# Prerequisites (see docs/superpowers/specs/2026-07-30-devx-sandbox-hardening-design.md):
#   - Ubuntu or Debian host
#   - systemd as the host's process manager (WSL2: /etc/wsl.conf -> [boot] systemd=true)
#   - Docker Engine installed natively (not via snap)
#   - Kernel >= 5.12 (>= 6.3 needed to skip shiftfs entirely)
#
# This script installs packages, may restart the host's Docker daemon, and
# will remove ALL existing Docker containers (a sysbox installer requirement).
# Run it interactively; do not run it unattended.
set -euo pipefail

SYSBOX_VERSION="0.7.0"

if [ "$(ps -p 1 -o comm=)" != "systemd" ]; then
    echo "Error: systemd is not PID 1 on this host. Enable it via /etc/wsl.conf" >&2
    echo "([boot] systemd=true) and restart WSL (wsl --shutdown from Windows), then retry." >&2
    exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker is not installed. Install Docker Engine natively before running this script." >&2
    exit 1
fi

if ! dpkg -l 2>/dev/null | grep -q '^ii.*docker-ce '; then
    echo "Warning: docker-ce package not found via dpkg. Sysbox requires a native (non-snap) Docker install." >&2
fi

arch="$(dpkg --print-architecture)"
case "$arch" in
    amd64|arm64) ;;
    *) echo "Error: unsupported architecture for sysbox: $arch" >&2; exit 1 ;;
esac

deb_name="sysbox-ce_${SYSBOX_VERSION}.linux_${arch}.deb"
deb_url="https://github.com/nestybox/sysbox/releases/download/v${SYSBOX_VERSION}/${deb_name}"
tmp_deb="/tmp/${deb_name}"

echo "Downloading ${deb_url}..."
curl -fsSL "$deb_url" -o "$tmp_deb"

echo "Installing prerequisites (jq)..."
sudo apt-get update
sudo apt-get install -y jq

existing="$(docker ps -a -q || true)"
if [ -n "$existing" ]; then
    echo ""
    echo "The sysbox installer requires removing ALL existing Docker containers"
    echo "(running and stopped) on this host. The following will be removed:"
    docker ps -a --format '  %s (%s)' 2>/dev/null || docker ps -a
    if [ "${ASSUME_YES:-}" != "1" ]; then
        read -r -p "Continue and remove them? [y/N] " reply
        case "$reply" in
            [yY][eE][sS]|[yY]) ;;
            *) echo "Aborted. Set ASSUME_YES=1 to skip this prompt."; exit 1 ;;
        esac
    fi
    docker rm -f $existing
fi

echo "Installing sysbox-ce..."
sudo apt-get install -y "$tmp_deb"

echo "Verifying sysbox service..."
systemctl status sysbox --no-pager -n 20

echo "Verifying Docker registered the sysbox-runc runtime..."
if docker info --format '{{json .Runtimes}}' | grep -q sysbox-runc; then
    echo "sysbox-runc is registered with Docker."
else
    echo "Error: sysbox-runc runtime not found in 'docker info'." >&2
    exit 1
fi

rm -f "$tmp_deb"
echo "Sysbox installation complete."
```

- [ ] **Step 2: Verify script syntax**

Run: `bash -n scripts/install-sysbox.sh`
Expected: no output, exit code 0.

- [ ] **Step 3: Make it executable and commit**

```bash
chmod +x scripts/install-sysbox.sh
git add scripts/install-sysbox.sh
git commit -m "Add host install script for sysbox-runc runtime"
```

- [ ] **Step 4 (⚠️ MANUAL — confirm with the user before running): Run the script against the real host**

```bash
wsl.exe -- bash /mnt/c/source/devx/scripts/install-sysbox.sh
```

This installs a systemd service and will prompt before removing any existing containers. Confirm with the user before running — it affects the entire Docker host, not just this project.

Expected: ends with `Sysbox installation complete.` and `sysbox-runc is registered with Docker.`

---

### Task 2: Sysbox nested-Docker spike (de-risk before wiring devx around it)

**Files:** none — host-level verification only, no repo changes.

**Interfaces:**
- Consumes: `sysbox-runc` runtime registered on the host (Task 1).
- Produces: confidence that `runtime: sysbox-runc` + nested `dockerd` works on this exact host/Docker combination before Tasks 3–10 build on that assumption.

- [ ] **Step 1 (⚠️ MANUAL — confirm with the user before running): Start a throwaway sysbox + DinD container**

```bash
wsl.exe -- docker run -d --name sysbox-spike --runtime=sysbox-runc docker:27-dind
```

Expected: prints a container ID, no error.

- [ ] **Step 2: Verify nested Docker works**

```bash
wsl.exe -- docker exec sysbox-spike docker version
wsl.exe -- docker exec sysbox-spike docker run --rm hello-world
```

Expected: `docker version` prints both Client and Server sections; `hello-world` prints "Hello from Docker!".

- [ ] **Step 3: Verify the container's root does NOT map to host root**

```bash
wsl.exe -- bash -c "PID=\$(docker inspect -f '{{.State.Pid}}' sysbox-spike); ps -o pid,uid,comm -p \$PID"
```

Expected: the UID column shows a large unprivileged number (sysbox's subuid range, e.g. 165536+), not `0`.

- [ ] **Step 4: Clean up**

```bash
wsl.exe -- docker rm -f sysbox-spike
```

If Step 2 or Step 3 fails, stop here and re-evaluate before proceeding to Task 3 — the rest of this plan assumes this mechanism works.

---

### Task 3: Full Docker Engine in the image + build-time docker group

**Files:**
- Modify: `core/Dockerfile:78-94` (step 4b), `core/Dockerfile:130-136` (step 8)

**Interfaces:**
- Produces: `dockerd`, `containerd`, `containerd-shim-runc-v2`, `runc`, `ctr`, `docker-init`, `docker-proxy`, `docker` all present in `/usr/local/bin`; a `docker` system group with `devx` as a member. Task 5 (entrypoint) depends on `dockerd` being present and on the `docker` group existing so `devx` can use the nested socket without sudo.

- [ ] **Step 1: Replace the Docker CLI-only install with a full Docker Engine install**

Replace the current step 4b block:

```dockerfile
# 4b. Install Docker CLI (daemonless; talks to host via /var/run/docker.sock)
ARG DOCKER_CLI_VERSION=27.5.1
ARG DOCKER_COMPOSE_VERSION=2.33.1
RUN set -eux; \
        arch="$(dpkg --print-architecture)"; \
        case "$arch" in \
            amd64) docker_arch='x86_64' ;; \
            arm64) docker_arch='aarch64' ;; \
            *) echo "Unsupported architecture for Docker CLI: $arch"; exit 1 ;; \
        esac; \
        curl -fsSL "https://download.docker.com/linux/static/stable/${docker_arch}/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz; \
        tar -xzf /tmp/docker.tgz -C /tmp; \
        install -m 0755 /tmp/docker/docker /usr/local/bin/docker; \
    mkdir -p /usr/local/lib/docker/cli-plugins; \
    curl -fsSL "https://github.com/docker/compose/releases/download/v${DOCKER_COMPOSE_VERSION}/docker-compose-linux-${docker_arch}" -o /usr/local/lib/docker/cli-plugins/docker-compose; \
    chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose; \
        rm -rf /tmp/docker /tmp/docker.tgz
```

with:

```dockerfile
# 4b. Install Docker Engine (dockerd + CLI; runs a nested daemon isolated by
# the sysbox-runc runtime — see docs/superpowers/specs/2026-07-30-devx-sandbox-hardening-design.md)
ARG DOCKER_CLI_VERSION=27.5.1
ARG DOCKER_COMPOSE_VERSION=2.33.1
RUN apt-get update && apt-get install -y --no-install-recommends iptables \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
        arch="$(dpkg --print-architecture)"; \
        case "$arch" in \
            amd64) docker_arch='x86_64' ;; \
            arm64) docker_arch='aarch64' ;; \
            *) echo "Unsupported architecture for Docker Engine: $arch"; exit 1 ;; \
        esac; \
        curl -fsSL "https://download.docker.com/linux/static/stable/${docker_arch}/docker-${DOCKER_CLI_VERSION}.tgz" -o /tmp/docker.tgz; \
        tar -xzf /tmp/docker.tgz -C /tmp; \
        install -m 0755 /tmp/docker/* /usr/local/bin/; \
    mkdir -p /usr/local/lib/docker/cli-plugins; \
    curl -fsSL "https://github.com/docker/compose/releases/download/v${DOCKER_COMPOSE_VERSION}/docker-compose-linux-${docker_arch}" -o /usr/local/lib/docker/cli-plugins/docker-compose; \
    chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose; \
        rm -rf /tmp/docker /tmp/docker.tgz
```

(`iptables` is required for the nested `dockerd`'s default bridge networking and `-p` port publishing, which the SSH-tunnel workflow in Task 9 depends on. `install -m 0755 /tmp/docker/*` installs every binary in Docker's static tarball, not just the CLI.)

- [ ] **Step 2: Add the `docker` group and add `devx` to it**

In step 8 of `core/Dockerfile`, change:

```dockerfile
# 8. Create devx user with home at /devx
RUN useradd -d /devx -s /bin/bash devx \
    && echo "root:devx" | chpasswd \
    && echo "devx:devx" | chpasswd \
    && usermod -aG sudo devx \
    && echo "devx ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
    && git config --global init.defaultBranch main
```

to:

```dockerfile
# 8. Create devx user with home at /devx
RUN useradd -d /devx -s /bin/bash devx \
    && echo "root:devx" | chpasswd \
    && echo "devx:devx" | chpasswd \
    && usermod -aG sudo devx \
    && echo "devx ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers \
    && groupadd -f docker \
    && usermod -aG docker devx \
    && git config --global init.defaultBranch main
```

- [ ] **Step 3: Build the image standalone and verify the binaries**

```bash
docker build --target devx -t devx-dockerfile-test core
docker run --rm devx-dockerfile-test dockerd --version
docker run --rm devx-dockerfile-test containerd --version
docker run --rm devx-dockerfile-test runc --version
docker run --rm devx-dockerfile-test id devx
```

Expected: each `--version` prints a version string (no "not found" errors); `id devx` output includes `docker` in the group list, e.g. `groups=... ,docker`.

- [ ] **Step 4: Commit**

```bash
git add core/Dockerfile
git commit -m "Install full Docker Engine in image instead of CLI-only"
```

---

### Task 4: SSH hardening (key-auth only)

**Files:**
- Modify: `core/Dockerfile:125-128` (step 7)

**Interfaces:**
- Produces: `sshd_config` with `PermitRootLogin no` and `PasswordAuthentication no`. No other task depends on this directly; Task 7 verifies it behaviorally end-to-end.

- [ ] **Step 1: Disable password auth and root login**

Change:

```dockerfile
# 7. Configure SSH (Key-auth + Password fallback)
RUN mkdir -p /var/run/sshd \
    && sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
```

to:

```dockerfile
# 7. Configure SSH (key-auth only)
RUN mkdir -p /var/run/sshd \
    && sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config \
    && sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
```

- [ ] **Step 2: Build and verify the config took effect**

```bash
docker build --target devx -t devx-dockerfile-test core
docker run -d --name sshd-config-test devx-dockerfile-test /usr/sbin/sshd -D
docker exec sshd-config-test grep -E '^(PermitRootLogin|PasswordAuthentication)' /etc/ssh/sshd_config
docker rm -f sshd-config-test
```

Expected output from the `grep`:
```
PermitRootLogin no
PasswordAuthentication no
```

- [ ] **Step 3: Commit**

```bash
git add core/Dockerfile
git commit -m "Disable SSH password auth and root login in sandbox image"
```

---

### Task 5: entrypoint.sh — start nested Docker, drop host-socket GID mapping

**Files:**
- Modify: `core/entrypoint.sh`

**Interfaces:**
- Consumes: `dockerd` binary and `docker` group from Task 3.
- Produces: a running nested `dockerd` with its socket owned by group `docker` before `sshd` starts accepting connections. Tasks 7–10 depend on this being up before SSH sessions try to use `docker`.

- [ ] **Step 1: Replace the host-socket GID-mapping block with nested dockerd startup**

Change:

```bash
# If the host Docker socket is mounted, map its group/GID so 'devx' can use it.
if [ -S /var/run/docker.sock ]; then
    SOCKET_GID="$(stat -c '%g' /var/run/docker.sock)"
    SOCKET_GROUP="$(getent group "${SOCKET_GID}" | cut -d: -f1 || true)"

    if [ -n "${SOCKET_GROUP}" ]; then
        usermod -aG "${SOCKET_GROUP}" devx || true
    else
        if getent group docker >/dev/null 2>&1; then
            groupmod -g "${SOCKET_GID}" docker 2>/dev/null || true
        else
            groupadd -g "${SOCKET_GID}" docker 2>/dev/null || true
        fi
        usermod -aG docker devx || true
    fi
fi
```

to:

```bash
# Start the nested Docker daemon. It's isolated from the host's Docker by the
# sysbox-runc runtime this container runs under — see
# docs/superpowers/specs/2026-07-30-devx-sandbox-hardening-design.md
mkdir -p /var/log/devx
dockerd >/var/log/devx/dockerd.log 2>&1 &

echo "Waiting for nested Docker daemon to be ready..."
for i in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then
        echo "Nested Docker daemon is ready."
        break
    fi
    sleep 1
done

if ! docker info >/dev/null 2>&1; then
    echo "Warning: nested Docker daemon did not become ready in time. See /var/log/devx/dockerd.log." >&2
fi
```

- [ ] **Step 2: Verify script syntax and that the old block is gone**

```bash
bash -n core/entrypoint.sh
grep -c "docker.sock" core/entrypoint.sh
```

Expected: `bash -n` prints nothing (exit 0); `grep -c` prints `0` (no remaining references to the host socket path in the file).

Full functional verification (does `dockerd` actually come up cleanly under the real sysbox runtime) happens in Task 7 — starting it under plain `runc` here wouldn't prove much, since nested Docker's whole point is that it needs sysbox's isolation to work well.

- [ ] **Step 3: Commit**

```bash
git add core/entrypoint.sh
git commit -m "Start nested Docker daemon in entrypoint instead of mapping host socket GID"
```

---

### Task 6: docker-compose.yml — sysbox runtime, drop socket mount, persistent nested storage

**Files:**
- Modify: `core/docker-compose.yml`

**Interfaces:**
- Consumes: `runtime: sysbox-runc` registered on the host (Task 1).
- Produces: the `sandbox` service definition all later verification tasks stand up via `./devx up`.

- [ ] **Step 1: Update the compose file**

Change:

```yaml
services:
  sandbox:
    build:
      context: .
      dockerfile: Dockerfile
      target: devx
    hostname: devx
    volumes:
      - devx_workspace:/devx
      # Docker-outside-of-Docker: use host daemon from inside the sandbox.
      - /var/run/docker.sock:/var/run/docker.sock
    ports:
      - "127.0.0.1:${DEVX_SSH_PORT:-2222}:22"
    restart: unless-stopped
    environment:
      - UV_PYTHON_INSTALL_DIR=/opt/python
      # Optional service environment should be merged from additional compose files.

volumes:
  devx_workspace:
```

to:

```yaml
services:
  sandbox:
    build:
      context: .
      dockerfile: Dockerfile
      target: devx
    hostname: devx
    runtime: sysbox-runc
    volumes:
      - devx_workspace:/devx
      # Nested Docker Engine data (images/containers the agent builds/runs),
      # isolated from the host's Docker by the sysbox-runc runtime above.
      - devx_docker_data:/var/lib/docker
    ports:
      - "127.0.0.1:${DEVX_SSH_PORT:-2222}:22"
    restart: unless-stopped
    environment:
      - UV_PYTHON_INSTALL_DIR=/opt/python
      # Optional service environment should be merged from additional compose files.

volumes:
  devx_workspace:
  devx_docker_data:
```

- [ ] **Step 2: Validate the compose file**

```bash
docker compose -f core/docker-compose.yml config
```

Expected: valid YAML output; the `sandbox` service shows `runtime: sysbox-runc`; no line contains `docker.sock`.

- [ ] **Step 3: Commit**

```bash
git add core/docker-compose.yml
git commit -m "Run sandbox under sysbox-runc with nested Docker storage instead of host socket mount"
```

---

### Task 7: End-to-end boot verification (nested Docker, SSH hardening, host isolation)

**Files:** none — verification only.

**Interfaces:**
- Consumes: Tasks 3–6 (built image, updated entrypoint and compose).

- [ ] **Step 1: Rebuild and start the sandbox**

```bash
./devx down
./devx up
```

Expected: ends with `Sandbox is up and running.` and `Connect with: ssh devx`.

- [ ] **Step 2: Verify SSH password auth is actually rejected**

```bash
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no -o BatchMode=yes -o ConnectTimeout=5 devx echo test 2>&1 | tee /tmp/ssh-pw-test.log
grep -c "password" /tmp/ssh-pw-test.log
```

Expected: the command fails (non-zero exit); the denial message lists only `publickey` as an available method (e.g. `Permission denied (publickey).`) — `grep -c "password"` should print `0`, confirming the server never offered password as an option.

- [ ] **Step 3: Verify nested Docker works from inside the sandbox**

```bash
ssh devx "docker version"
ssh devx "docker run --rm hello-world"
```

Expected: `docker version` shows Client and Server sections; `hello-world` prints "Hello from Docker!".

- [ ] **Step 4: Verify the nested container is invisible to the host's Docker**

```bash
ssh devx "docker run -d --name isolation-probe nginx:alpine"
wsl.exe -- docker ps --filter name=isolation-probe --format '{{.Names}}'
```

Expected: the `wsl.exe -- docker ps` command prints nothing (empty output) — the container exists only inside the sandbox's nested daemon, not on the host's.

```bash
ssh devx "docker rm -f isolation-probe"
```

- [ ] **Step 5: Verify sandbox root maps to an unprivileged host UID**

```bash
wsl.exe -- bash -c "PID=\$(docker compose -f /mnt/c/source/devx/core/docker-compose.yml -p devx ps -q sandbox); ps -o pid,uid,comm -p \$PID"
```

Expected: the UID column shows a large unprivileged number, not `0`.

No commit for this task — it produces no file changes, only confirms Tasks 3–6 work together.

---

### Task 8: Persistence verification (nested image cache survives `devx down`/`up`)

**Files:** none — verification only.

- [ ] **Step 1: Build an image inside the sandbox**

```bash
ssh devx 'printf "FROM alpine:3.20\nRUN echo hello > /hello.txt\n" | docker build -t persistence-test -'
ssh devx "docker images persistence-test --format '{{.Repository}}:{{.Tag}}'"
```

Expected: prints `persistence-test:latest`.

- [ ] **Step 2: Cycle the sandbox**

```bash
./devx down
./devx up
```

- [ ] **Step 3: Confirm the image survived**

```bash
ssh devx "docker images persistence-test --format '{{.Repository}}:{{.Tag}}'"
```

Expected: still prints `persistence-test:latest`, with no rebuild — proves `devx_docker_data` volume persistence.

No commit for this task.

---

### Task 9: SSH tunnel verification (ad hoc port access)

**Files:** none — verification only.

- [ ] **Step 1: Publish a port from a nested container**

```bash
ssh devx "docker run -d --name tunnel-test -p 8099:80 nginx:alpine"
```

- [ ] **Step 2: Open a tunnel and reach it**

```bash
ssh -f -N -L 8099:localhost:8099 devx
curl -sf -o /dev/null -w "%{http_code}\n" http://localhost:8099
```

Expected: prints `200`.

- [ ] **Step 3: Clean up**

```bash
pkill -f "ssh -f -N -L 8099:localhost:8099 devx" || true
ssh devx "docker rm -f tunnel-test"
```

No commit for this task.

---

### Task 10: Multi-instance verification

**Files:** none — verification only.

- [ ] **Step 1: Start a second instance**

```bash
./devx up -i devx2 -p 2223
```

Expected: ends with `Connect with: ssh devx-devx2`.

- [ ] **Step 2: Verify independent nested Docker per instance**

```bash
ssh devx-devx2 "docker run --rm hello-world"
ssh devx "docker run -d --name instance-marker alpine sleep 300"
ssh devx-devx2 "docker ps --filter name=instance-marker --format '{{.Names}}'"
```

Expected: `hello-world` succeeds on `devx-devx2`; the last command prints nothing — `devx2`'s nested Docker doesn't see the first instance's container.

- [ ] **Step 3: Clean up**

```bash
ssh devx "docker rm -f instance-marker"
./devx down -i devx2 -p 2223
```

No commit for this task.

---

### Task 11: ztp overlay — remove hardcoded credentials

**Files:**
- Modify: `core/docker-compose.ztp.yml`

**Interfaces:** none consumed/produced beyond this file — independent cleanup.

- [ ] **Step 1: Make credentials overridable and fix the wildcard allowed-hosts**

Change:

```yaml
  temporal:
    image: temporalio/auto-setup:latest
    depends_on:
      - temporal-postgres
    ports:
      - "127.0.0.1:${DEVX_TEMPORAL_GRPC_PORT:-7233}:7233"
    environment:
      DB: postgres12
      DB_PORT: "5432"
      POSTGRES_USER: temporal
      POSTGRES_PWD: temporal
```

to:

```yaml
  temporal:
    image: temporalio/auto-setup:latest
    depends_on:
      - temporal-postgres
    ports:
      - "127.0.0.1:${DEVX_TEMPORAL_GRPC_PORT:-7233}:7233"
    environment:
      DB: postgres12
      DB_PORT: "5432"
      POSTGRES_USER: temporal
      POSTGRES_PWD: ${DEVX_TEMPORAL_DB_PASSWORD:-temporal}
```

Change:

```yaml
  temporal-postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: temporal
      POSTGRES_USER: temporal
      POSTGRES_PASSWORD: temporal
```

to:

```yaml
  temporal-postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: temporal
      POSTGRES_USER: temporal
      POSTGRES_PASSWORD: ${DEVX_TEMPORAL_DB_PASSWORD:-temporal}
```

Change:

```yaml
  ztp-postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: nautobot
      POSTGRES_USER: nautobot
      POSTGRES_PASSWORD: nautobot
```

to:

```yaml
  ztp-postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: nautobot
      POSTGRES_USER: nautobot
      POSTGRES_PASSWORD: ${DEVX_NAUTOBOT_DB_PASSWORD:-nautobot}
```

Change:

```yaml
  nautobot:
    image: networktocode/nautobot:2.4.21
    ports:
      - "127.0.0.1:${DEVX_NAUTOBOT_PORT:-8889}:8080"
    environment:
      NAUTOBOT_DB_ENGINE: django.db.backends.postgresql
      NAUTOBOT_DB_NAME: nautobot
      NAUTOBOT_DB_USER: nautobot
      NAUTOBOT_DB_PASSWORD: nautobot
      NAUTOBOT_DB_HOST: ztp-postgres
      NAUTOBOT_REDIS_HOST: ztp-redis
      NAUTOBOT_ALLOWED_HOSTS: "*"
      NAUTOBOT_SECRET_KEY: dev-secret-key-change-in-prod
      NAUTOBOT_CREATE_SUPERUSER: "true"
      NAUTOBOT_SUPERUSER_NAME: admin
      NAUTOBOT_SUPERUSER_PASSWORD: admin
      NAUTOBOT_SUPERUSER_API_TOKEN: admin-token
```

to:

```yaml
  nautobot:
    image: networktocode/nautobot:2.4.21
    ports:
      - "127.0.0.1:${DEVX_NAUTOBOT_PORT:-8889}:8080"
    environment:
      NAUTOBOT_DB_ENGINE: django.db.backends.postgresql
      NAUTOBOT_DB_NAME: nautobot
      NAUTOBOT_DB_USER: nautobot
      NAUTOBOT_DB_PASSWORD: ${DEVX_NAUTOBOT_DB_PASSWORD:-nautobot}
      NAUTOBOT_DB_HOST: ztp-postgres
      NAUTOBOT_REDIS_HOST: ztp-redis
      NAUTOBOT_ALLOWED_HOSTS: "127.0.0.1,localhost,nautobot"
      NAUTOBOT_SECRET_KEY: ${DEVX_NAUTOBOT_SECRET_KEY:-dev-secret-key-change-in-prod}
      NAUTOBOT_CREATE_SUPERUSER: "true"
      NAUTOBOT_SUPERUSER_NAME: admin
      NAUTOBOT_SUPERUSER_PASSWORD: ${DEVX_NAUTOBOT_SUPERUSER_PASSWORD:-admin}
      NAUTOBOT_SUPERUSER_API_TOKEN: ${DEVX_NAUTOBOT_SUPERUSER_API_TOKEN:-admin-token}
```

- [ ] **Step 2: Validate the compose file and confirm defaults are unchanged**

```bash
docker compose -f core/docker-compose.yml -f core/docker-compose.ztp.yml config | grep -E "POSTGRES_PWD|POSTGRES_PASSWORD|NAUTOBOT_ALLOWED_HOSTS|NAUTOBOT_SECRET_KEY|NAUTOBOT_SUPERUSER_PASSWORD|NAUTOBOT_SUPERUSER_API_TOKEN"
```

Expected: valid YAML output; values resolve to the same defaults as before (`temporal`, `nautobot`, `admin`, `admin-token`, `dev-secret-key-change-in-prod`) since no override env vars are set — confirms this is backward compatible, just now overridable.

- [ ] **Step 3: Commit**

```bash
git add core/docker-compose.ztp.yml
git commit -m "Make ztp overlay credentials overridable and fix wildcard allowed-hosts"
```

---

### Task 12: Fix shell-interpolation pattern in `inject_ssh_key`

**Files:**
- Modify: `cli/devx.py:73-81`

**Interfaces:** none consumed/produced beyond this file — independent cleanup.

- [ ] **Step 1: Rewrite to pass the key via stdin instead of string interpolation**

Change:

```python
def inject_ssh_key(pub_key, container_id):
    """Injects the public key into the running sandbox container for both root and devx users."""
    print("Injecting SSH public key into container...")
    for user in ["root", "devx"]:
        home = "/root" if user == "root" else "/devx"
        run_command(["docker", "exec", container_id, "mkdir", "-p", f"{home}/.ssh"])
        # Use grep to check if key already exists before appending
        cmd = f"grep -qF '{pub_key}' {home}/.ssh/authorized_keys || echo '{pub_key}' >> {home}/.ssh/authorized_keys && chmod 600 {home}/.ssh/authorized_keys && chown -R {user}:{user} {home}/.ssh"
        run_command(["docker", "exec", container_id, "sh", "-c", cmd])
```

to:

```python
def inject_ssh_key(pub_key, container_id):
    """Injects the public key into the running sandbox container for both root and devx users."""
    print("Injecting SSH public key into container...")
    script = (
        'set -e; '
        'key="$(cat)"; '
        'mkdir -p "$1/.ssh"; '
        'touch "$1/.ssh/authorized_keys"; '
        'grep -qF "$key" "$1/.ssh/authorized_keys" || echo "$key" >> "$1/.ssh/authorized_keys"; '
        'chmod 600 "$1/.ssh/authorized_keys"; '
        'chown -R "$2:$2" "$1/.ssh"'
    )
    for user in ["root", "devx"]:
        home = "/root" if user == "root" else "/devx"
        subprocess.run(
            ["docker", "exec", "-i", container_id, "sh", "-c", script, "_", home, user],
            input=pub_key,
            text=True,
            check=True,
            capture_output=True,
        )
```

(The key value now travels via stdin — read with `key="$(cat)"` — instead of being embedded into the shell command string, so its contents can never be interpreted as shell syntax. `home` and `user` arrive as positional parameters `$1`/`$2`, `_` fills the conventional `$0` slot.)

- [ ] **Step 2: Verify against a running container, including with a crafted key containing shell metacharacters**

```bash
./devx up
python3 -c "
import sys
sys.path.insert(0, 'cli')
from devx import inject_ssh_key, get_sandbox_container_id
import os
core_dir = os.path.join(os.getcwd(), 'core')
cid = get_sandbox_container_id(core_dir, [], 'devx', 2222, 5432)
crafted = 'ssh-ed25519 AAAATEST\$(touch /tmp/pwned) test@example.com'
inject_ssh_key(crafted, cid)
"
docker exec "$(docker compose -f core/docker-compose.yml -p devx ps -q sandbox)" cat /devx/.ssh/authorized_keys
docker exec "$(docker compose -f core/docker-compose.yml -p devx ps -q sandbox)" test -f /tmp/pwned && echo "FAIL: command substitution executed" || echo "PASS: key stored literally, nothing executed"
```

Expected: the `authorized_keys` file contains the crafted string verbatim, including the literal `$(touch /tmp/pwned)` text; the final check prints `PASS: key stored literally, nothing executed`.

- [ ] **Step 3: Re-run normal key injection to confirm the real flow still works**

```bash
./devx down
./devx up
ssh devx echo "SSH still works"
```

Expected: prints `SSH still works`.

- [ ] **Step 4: Commit**

```bash
git add cli/devx.py
git commit -m "Pass SSH pubkey via stdin instead of shell string interpolation"
```

---

### Task 13: Update README

**Files:**
- Modify: `README.md:31-33` (Prerequisites), `README.md:51-67` ("Running Docker from inside DevX")

**Interfaces:** none — documentation only.

- [ ] **Step 1: Note the sysbox host prerequisite**

Change:

```markdown
### Prerequisites
- **Windows**: WSL2 (Ubuntu) and Docker Desktop (or native Docker inside WSL2).
- **macOS/Linux**: Docker and Python 3.
```

to:

```markdown
### Prerequisites
- **Windows**: WSL2 (Ubuntu) and Docker Desktop (or native Docker inside WSL2).
- **macOS/Linux**: Docker and Python 3.
- **All platforms**: the `sysbox-runc` container runtime installed on the Docker host — see [Running Docker from inside DevX](#running-docker-from-inside-devx).
```

- [ ] **Step 2: Replace the DooD section with the sysbox DinD section**

Change:

```markdown
### Running Docker from inside DevX
DevX supports Docker-outside-of-Docker so projects inside the sandbox can use the host Docker daemon.

- The sandbox mounts `/var/run/docker.sock`.
- The image includes the Docker CLI binary and Docker Compose plugin.
- On startup, DevX maps the socket's GID to a group and adds `devx` to that group automatically.

Verify inside the sandbox:

```bash
ls -la /var/run/docker.sock
docker version
docker compose version
docker ps
```

Security note: mounting the Docker socket gives the container root-equivalent control over the host Docker daemon. Only use this with trusted code and trusted users.
```

to:

```markdown
### Running Docker from inside DevX
DevX runs its own isolated Docker Engine inside the sandbox (Docker-in-Docker), instead of mounting the host's Docker socket. Containers, images, and builds the agent creates stay inside the sandbox and cannot reach or control the host's Docker daemon.

**One-time host setup:** the sandbox runs under the `sysbox-runc` runtime, which must be installed on the host once:

```bash
./scripts/install-sysbox.sh
```

See `docs/superpowers/specs/2026-07-30-devx-sandbox-hardening-design.md` for why this approach was chosen over mounting the host socket.

Verify inside the sandbox:

```bash
docker version
docker compose version
docker run --rm hello-world
```

**Reaching a container's published port from your host:** ports the agent publishes with `docker run -p` are only reachable from inside the sandbox's own network namespace, not automatically from Windows/macOS/Linux. Use an SSH tunnel:

```bash
ssh -L 8080:localhost:8080 devx
```

Then open `http://localhost:8080` normally. If you use VS Code Remote-SSH to connect to `devx`, it detects listening ports on the remote and offers to forward them automatically — no manual tunnel needed.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document sysbox-based Docker-in-Docker and SSH tunnel port access"
```

---

## Self-Review Notes

- **Spec coverage:** host prereqs (Task 1), DinD mechanism validation (Task 2), Dockerfile Docker Engine + docker group (Task 3), SSH hardening (Task 4), entrypoint nested dockerd (Task 5), compose runtime/volume/socket removal (Task 6), full behavioral + isolation + UID-mapping verification (Task 7), persistence (Task 8), SSH tunnel workflow (Task 9), multi-instance (Task 10), ztp creds cleanup (Task 11), `inject_ssh_key` hygiene fix (Task 12), README (Task 13). All spec sections are covered; no gaps found.
- **Placeholder scan:** no TBD/TODO markers; every step has literal file content or literal commands with concrete expected output.
- **Type/name consistency:** `devx_docker_data` volume name matches between Task 6 (compose) and Task 8/9 (referenced conceptually, not by name, in verification — no drift). `install-sysbox.sh` path matches between Task 1 (create), Task 13 (README reference). Host alias `devx-devx2` in Task 10 matches the exact string `cli/devx.py`'s `host_alias = "devx" if instance == "devx" else f"devx-{instance}"` produces for `-i devx2`.
