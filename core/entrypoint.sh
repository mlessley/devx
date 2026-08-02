#!/bin/bash
# --- DevX Entrypoint Script ---

# Ensure the repos directory exists on the volume
mkdir -p /devx/repos

# Copy skeleton files if they don't exist (since /devx is a volume)
if [ ! -f /devx/.bashrc ]; then
    echo "Initializing /devx with default dotfiles..."
    cp /etc/skel/.bashrc /devx/.bashrc
    cp /etc/skel/.profile /devx/.profile
    cp /etc/skel/.bash_logout /devx/.bash_logout
fi

# Seed Claude Code defaults once for new home volumes
if [ ! -f /devx/.clauderc ]; then
    cat << 'EOF' > /devx/.clauderc
{
  "sandbox": true,
  "allowUnsandboxedCommands": false,
  "theme": "dark"
}
EOF
fi

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

# Inject a high-performance, informative prompt
if ! grep -q "DevX 2.0 Prompt Configuration" /devx/.bashrc; then
    cat << 'EOF' >> /devx/.bashrc

# DevX 2.0 Prompt Configuration
# [Time] devx@devx [Dir] (git:branch)
parse_git_branch() {
     git branch 2> /dev/null | sed -e '/^[^*]/d' -e 's/* \(.*\)/ (\1)/'
}
export PS1="\[\033[01;30m\][\t] \[\033[01;32m\]devx@devx \[\033[01;34m\]\w\[\033[01;35m\]\$(parse_git_branch)\[\033[00m\] \$ "
EOF
fi

# Fix permissions for the devx user for the entire home directory
chown -R devx:devx /devx

# Configure git default branch for the devx user
sudo -u devx git config --global init.defaultBranch main

# Execute the original command (sshd)
exec "$@"
