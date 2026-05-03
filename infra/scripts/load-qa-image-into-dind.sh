#!/usr/bin/env bash
# Build the QA sandbox image and load it into the running DinD container.
# Run from the repo root; expects an Athanor stack already up (DinD healthy).
set -euo pipefail

cd "$(dirname "$0")/../.."

echo "Building athanor-sandbox-qa:latest..."
docker build -t athanor-sandbox-qa:latest -f infra/Dockerfile.sandbox.qa .

echo "Loading into DinD..."
cd infra
docker save athanor-sandbox-qa:latest \
    | docker compose exec -T orchestrator docker \
        --host tcp://dind:2376 --tlsverify \
        --tlscacert=/certs/client/ca.pem \
        --tlscert=/certs/client/cert.pem \
        --tlskey=/certs/client/key.pem \
        load

echo "Done. Verifying tag inside DinD..."
docker compose exec -T orchestrator docker \
    --host tcp://dind:2376 --tlsverify \
    --tlscacert=/certs/client/ca.pem \
    --tlscert=/certs/client/cert.pem \
    --tlskey=/certs/client/key.pem \
    images athanor-sandbox-qa:latest
