#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run this script with sudo." >&2
    exit 1
fi

. /etc/os-release
if [[ $ID != ubuntu || $VERSION_ID != 24.04 ]]; then
    echo "This bootstrap expects Ubuntu 24.04 LTS." >&2
    exit 1
fi

if [[ ! -e /swapfile ]]; then
    install -m 0600 /dev/null /swapfile
    fallocate -l 2G /swapfile
    mkswap /swapfile
fi
chmod 0600 /swapfile
if ! swapon --show=NAME --noheadings --raw | grep -Fxq /swapfile; then
    swapon /swapfile
fi
if ! grep -Eq '^/swapfile[[:space:]]' /etc/fstab; then
    printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
fi

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
    apt-get update
    apt-get install -y ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

cat <<'EOF'
From the release checkout, follow DEPLOY.md to configure .env.prod and build labeled images.
Dispatch and reconciliation start by default. Set OUTBOX_WORKER_ENABLED=false to disable both.
EOF
