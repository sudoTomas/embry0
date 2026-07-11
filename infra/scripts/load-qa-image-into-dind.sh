#!/usr/bin/env bash
# Build the QA sandbox image and load it into the running DinD container.
# Run from the repo root; expects an embry0 stack already up (DinD healthy).
set -euo pipefail

cd "$(dirname "$0")/../.."

echo "Building embry0-sandbox-qa:latest..."
docker build -t embry0-sandbox-qa:latest -f infra/Dockerfile.sandbox.qa .

echo "Loading into DinD..."
cd infra
docker save embry0-sandbox-qa:latest \
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
    images embry0-sandbox-qa:latest
