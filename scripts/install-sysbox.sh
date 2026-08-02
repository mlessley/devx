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
