#!/usr/bin/env bash
# Create isolated Docker networks for sandbox containers.
# Idempotent — safe to run multiple times.

set -euo pipefail

create_network() {
    local name=$1
    shift
    if docker network inspect "$name" &>/dev/null; then
        echo "Network '$name' already exists"
    else
        docker network create "$@" "$name"
        echo "Created network '$name'"
    fi
}

# sandbox-restricted: agents can ONLY reach proxy services
create_network sandbox-restricted \
    --driver bridge \
    --opt com.docker.network.bridge.enable_ip_masquerade=false

# sandbox-internet: filtered egress for research agents
create_network sandbox-internet \
    --driver bridge

echo "Sandbox networks ready."
