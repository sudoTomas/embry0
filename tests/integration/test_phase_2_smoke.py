"""Phase 2 end-to-end smoke against a fixture repo with .embry0/qa.yaml.

Requires:
- Live orchestrator with Phase 2 deployed
- DinD up; embry0-sandbox-qa:latest loaded
- MinIO up
- A repo+branch combo at QA_SMOKE_REPO/QA_SMOKE_BRANCH (default: a fixture
  branch that ships a tiny http-echo compose stack + .embry0/qa.yaml)

Skips cleanly when env vars are not set.
"""

from __future__ import annotations

import asyncio
import os

import pytest


@pytest.mark.requires_postgres
@pytest.mark.requires_minio
@pytest.mark.requires_dind
@pytest.mark.timeout(900)  # 15 min — first cold pull of macrolab-tier images
@pytest.mark.asyncio
async def test_qa_pipeline_against_fixture_repo(app, qa_minio_seeded):
    """End-to-end: POST /jobs pipeline=qa against the fixture branch,
    poll until terminal, assert overall=passed and artifacts exist."""

    repo = os.environ.get("QA_SMOKE_REPO")
    branch = os.environ.get("QA_SMOKE_BRANCH")
    if not repo or not branch:
        pytest.skip("QA_SMOKE_REPO + QA_SMOKE_BRANCH must be set")

    r = await app.post(
        "/api/v1/jobs",
        json={
            "repo": repo,
            "pipeline": "qa",
            "branch": branch,
            "qa": {"acceptance_criteria": ["echo server returns 200 on /health"]},
        },
    )
    assert r.status_code == 201, f"job create failed: {r.status_code} {r.text}"
    job_id = r.json()["job_id"]

    # Poll up to 12 minutes for terminal state.
    final = None
    for _ in range(72):
        r = await app.get(f"/api/v1/jobs/{job_id}")
        if r.status_code == 200:
            final = r.json()
            if final.get("status") in ("succeeded", "completed", "failed"):
                break
        await asyncio.sleep(10)

    assert final is not None, "job state never became readable"
    assert final["status"] in ("succeeded", "completed"), f"job did not succeed: {final}"

    # Verify artifacts landed in MinIO.
    objs = await qa_minio_seeded.list_objects("qa-artifacts", prefix=f"{job_id}/")
    assert any(o.endswith("result.json") for o in objs), f"no result.json in MinIO: {objs}"
    assert any(o.endswith("logs/full.log") for o in objs), f"no logs/full.log in MinIO: {objs}"
