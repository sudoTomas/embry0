#!/bin/sh
# Tag and push the host-built embry0-proxy + embry0-sandbox images to the
# sidecar registry so DinD (and a future K8s cluster) can pull them by name.
# Idempotent — re-running with the same images is a no-op.
#
# Network shape: this script runs in a sidecar container with the host's
# docker.sock mounted. Both `tag` and `push` are forwarded to the host docker
# daemon, which performs the push from the host's network namespace — reaching
# the registry via its loopback-bound port (default 127.0.0.1:5001). DinD
# pulls the same images by their backend-network name (registry:5000).
set -eu

REGISTRY="${REGISTRY:-127.0.0.1:5001}"
IMAGES="${IMAGES:-embry0-proxy embry0-sandbox embry0-sandbox-dev-python}"
TAG="${TAG:-latest}"

for image in $IMAGES; do
    src="${image}:${TAG}"
    dst="${REGISTRY}/${image}:${TAG}"
    if ! docker image inspect "$src" >/dev/null 2>&1; then
        echo "ERROR: ${src} not found in host docker daemon." >&2
        echo "Build it first: docker compose --profile images build" >&2
        exit 1
    fi
    docker tag "$src" "$dst"
    docker push "$dst"
    echo "pushed ${dst}"
done
