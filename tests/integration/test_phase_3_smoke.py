"""Phase 3 smoke — exercises the dashboard QA artifact endpoints.

Seeds a fake completed attempt's artifacts directly into MinIO and then hits
the four dashboard routes (list_attempts, get_result, generic artifact 302,
latest_screenshot 404 when no screenshots) end-to-end.

Skipped cleanly when MinIO isn't reachable (see tests/conftest.py for the
``requires_minio`` collection-time gate).
"""

from __future__ import annotations

import io

import pytest


@pytest.mark.requires_postgres
@pytest.mark.requires_minio
@pytest.mark.asyncio
async def test_phase_3_artifact_endpoints(app, qa_minio_seeded):
    """After artifacts are uploaded for a job, the dashboard endpoints
    surface them correctly: list returns the attempt, parsed result hits
    'passed', the generic artifact route 302s to a presigned URL, and the
    latest-screenshot probe 404s when no .png exists.

    The ``qa_minio_seeded`` fixture wires the client into ``app.state``
    so the routes' ``get_qa_minio`` dependency resolves it.
    """
    bucket = "qa-artifacts"
    job_id = "P3SMOKE"

    result = (
        b'{"schema_version": 1, "job_id": "P3SMOKE", "attempt_n": 1,'
        b' "phase_reached": "report", "overall": "passed",'
        b' "boot": {"command": "x", "duration_ms": 1, "ready_checks":'
        b' [{"url": "http://x", "status": 200, "duration_ms": 1}]},'
        b' "acceptance_results": [{"criterion": "home loads",'
        b' "status": "passed", "evidence": []}],'
        b' "anomalies": []}'
    )

    try:
        # Use the minio SDK directly via the seeded client. ensure_bucket
        # was called by the fixture so the bucket already exists.
        qa_minio_seeded._client.put_object(  # noqa: SLF001
            bucket,
            f"{job_id}/1/result.json",
            io.BytesIO(result),
            len(result),
            content_type="application/json",
        )

        # 1. List attempts surfaces attempt_n=1 with has_result_json=True.
        r = await app.get(f"/api/v1/jobs/{job_id}/qa/attempts")
        assert r.status_code == 200, r.text
        body = r.json()
        assert any(
            a["attempt_n"] == 1 and a["has_result_json"]
            for a in body["attempts"]
        ), body

        # 2. Per-attempt parsed result returns the JSON document.
        r = await app.get(f"/api/v1/jobs/{job_id}/qa/attempts/1/result")
        assert r.status_code == 200, r.text
        assert r.json()["overall"] == "passed"

        # 3. Generic artifact route resolves the latest attempt and 302s
        #    to a presigned MinIO GET URL.
        r = await app.get(
            f"/api/v1/jobs/{job_id}/artifacts/result.json",
            follow_redirects=False,
        )
        assert r.status_code == 302, r.text

        # 4. No .png uploaded under <job_id>/, so latest-screenshot 404s.
        r = await app.get(
            f"/api/v1/jobs/{job_id}/artifacts/screenshots/latest"
        )
        assert r.status_code == 404, r.text
    finally:
        # Clean up every seeded object so reruns don't accumulate junk.
        objs = await qa_minio_seeded.list_objects(bucket, prefix=f"{job_id}/")
        for key in objs:
            qa_minio_seeded._client.remove_object(bucket, key)  # noqa: SLF001
