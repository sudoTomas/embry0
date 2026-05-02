"""Phase 1 smoke test — boots a qa-jvm sandbox, registers a sandbox token,
hits /internal/qa/presign from inside the sandbox via curl, and runs
`docker info` against DinD to prove the certs work.

Requires:
- DinD up (athanor-dind container reachable from orchestrator)
- MinIO up (athanor-minio container; MINIO_ENDPOINT set)
- athanor-sandbox-qa:latest loaded into DinD (run infra/scripts/load-qa-image-into-dind.sh first)
- Live orchestrator running Phase 1 code (presign endpoint mounted, qa_minio + qa_token_registry on app.state)
"""

from __future__ import annotations

import json
import secrets

import pytest


@pytest.mark.requires_postgres
@pytest.mark.requires_minio
@pytest.mark.requires_dind
@pytest.mark.asyncio
async def test_qa_jvm_sandbox_can_presign_and_run_docker(app, qa_minio_seeded):
    """End-to-end: profile resolves, sandbox starts with DinD certs, can
    mint presigned URLs, can run `docker info` against DinD."""

    # 0. Precondition: this test reaches into orchestrator process state
    # (docker client, proxy manager, qa_token_registry) which the integration
    # `app` fixture's lifespan does NOT populate. Skip cleanly when run from
    # pytest outside the live orchestrator container; Task 15's deployment runs
    # this test via `docker exec orchestrator pytest ...` where state IS set.
    if not all(
        hasattr(app.app.state, attr)
        for attr in ("docker", "proxy_manager", "qa_token_registry")
    ):
        pytest.skip(
            "test requires live orchestrator process state — run via "
            "'docker exec orchestrator pytest tests/integration/test_phase_1_smoke.py'"
        )

    # 1. Get the qa-jvm profile from the API.
    r = await app.get("/api/v1/sandbox-profiles/qa-jvm")
    assert r.status_code == 200, f"qa-jvm profile missing: {r.status_code} {r.text}"
    profile = r.json()
    assert profile["dind_enabled"] is True

    # 2. Manually start a sandbox using SandboxManager.
    from athanor.execution.sandbox_manager import SandboxManager

    docker = app.app.state.docker
    proxy_mgr = app.app.state.proxy_manager
    mgr = SandboxManager(docker=docker, proxy_manager=proxy_mgr)

    job_id = f"smoke-{secrets.token_hex(4)}"
    container_id, sandbox_token = await mgr.create(job_id, profile=profile, env={})

    # Register token in the QA token registry so the presign endpoint resolves it.
    app.app.state.qa_token_registry.register(sandbox_token, job_id=job_id, attempt_n=1)

    try:
        # 3. From inside the sandbox, run docker info via the bind-mounted certs.
        result = await docker.run_cmd(
            docker.build_exec_cmd(container_id, ["docker", "info", "--format", "{{.ServerVersion}}"]),
            timeout=15,
        )
        assert result.strip(), "docker info returned empty — DinD certs not working"

        # 4. From inside the sandbox, hit /internal/qa/presign via curl against
        # the orchestrator on the backend network.
        curl_cmd = [
            "curl", "-sf",
            "-X", "POST",
            "-H", "Content-Type: application/json",
            "-d", json.dumps({"sandbox_token": sandbox_token, "paths": ["smoke/result.json"]}),
            "http://orchestrator:8000/api/v1/internal/qa/presign",
        ]
        body = await docker.run_cmd(
            docker.build_exec_cmd(container_id, curl_cmd),
            timeout=10,
        )
        body_json = json.loads(body)
        assert body_json["prefix"] == f"{job_id}/1/"
        assert len(body_json["urls"]) == 1
        assert body_json["urls"][0]["path"] == "smoke/result.json"

        # 5. Sandbox uploads via the presigned URL.
        upload_cmd = [
            "bash", "-c",
            f"echo '{{\"ok\": true}}' | curl -sf -X PUT --data-binary @- '{body_json['urls'][0]['url']}'",
        ]
        await docker.run_cmd(
            docker.build_exec_cmd(container_id, upload_cmd),
            timeout=10,
        )

        # 6. Verify the object landed in MinIO.
        objs = await qa_minio_seeded.list_objects("qa-artifacts", prefix=f"{job_id}/")
        assert any(o.endswith("smoke/result.json") for o in objs), \
            f"smoke/result.json not in {objs}"
    finally:
        # 7. Cleanup
        app.app.state.qa_token_registry.unregister(sandbox_token)
        await mgr.destroy(container_id)
