#!/usr/bin/env bash
# Create isolated Docker networks for sandbox containers.
# Idempotent — safe to run multiple times. If a network exists with the
# wrong options, the script exits non-zero so the operator notices.

set -euo pipefail

ensure_network_with_opts() {
    local name=$1
    local expected_masq=$2
    shift 2
    if docker network inspect "$name" >/dev/null 2>&1; then
        local actual_masq
        actual_masq=$(docker network inspect "$name" \
            --format '{{ index .Options "com.docker.network.bridge.enable_ip_masquerade" }}')
        if [ "$actual_masq" != "$expected_masq" ]; then
            echo "ERROR: network '$name' exists with enable_ip_masquerade=${actual_masq:-<unset>}" >&2
            echo "       expected '${expected_masq}'. Delete it and re-run:" >&2
            echo "         docker network rm $name" >&2
            exit 1
        fi
        echo "Network '$name' already exists with correct options"
    else
        docker network create "$@" "$name"
        echo "Created network '$name'"
    fi
}

# sandbox-restricted: agents can ONLY reach proxy services on this network
ensure_network_with_opts sandbox-restricted "false" \
    --driver bridge \
    --opt com.docker.network.bridge.enable_ip_masquerade=false

# sandbox-internet: filtered egress for proxies (and research agents, future)
ensure_network_with_opts sandbox-internet "" \
    --driver bridge

echo "Sandbox networks ready."
