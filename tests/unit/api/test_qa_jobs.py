"""Test POST /api/v1/jobs accepts pipeline='qa'."""

import pytest


@pytest.mark.asyncio
async def test_post_jobs_qa_pipeline_accepts_payload(api_client) -> None:
    """Schema accepts the QA payload shape."""
    payload = {
        "repo": "x/y",
        "pipeline": "qa",
        "branch": "main",
        "qa": {
            "acceptance_criteria": ["home loads"],
            "qa_timeout_seconds": 7200,
        },
    }
    r = await api_client.post("/api/v1/jobs", json=payload)
    # Status may be 201 (success), 500 (executor not wired yet for unit test),
    # or 503 (no executor). The point is NOT 422 (validation rejection).
    assert r.status_code != 422, f"Schema rejected the QA payload: {r.text}"


@pytest.mark.asyncio
async def test_post_jobs_qa_rejects_invalid_timeout(api_client) -> None:
    """qa_timeout_seconds must be > 0 and <= 86400."""
    payload = {
        "repo": "x/y",
        "pipeline": "qa",
        "branch": "main",
        "qa": {"qa_timeout_seconds": -1},
    }
    r = await api_client.post("/api/v1/jobs", json=payload)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_jobs_qa_rejects_unknown_qa_field(api_client) -> None:
    """extra='forbid' on QAJobOverrides catches typos."""
    payload = {
        "repo": "x/y",
        "pipeline": "qa",
        "branch": "main",
        "qa": {"unknown_field": "oops"},
    }
    r = await api_client.post("/api/v1/jobs", json=payload)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_jobs_qa_rejects_missing_branch(api_client) -> None:
    """qa pipeline requires a branch — missing one is a 400 from the handler."""
    payload = {
        "repo": "x/y",
        "pipeline": "qa",
        "qa": {"acceptance_criteria": []},
    }
    r = await api_client.post("/api/v1/jobs", json=payload)
    assert r.status_code == 400, r.text
    assert "branch" in r.text.lower()


@pytest.mark.asyncio
async def test_post_jobs_qa_rejects_invalid_timeout_zero(api_client) -> None:
    """qa_timeout_seconds=0 violates gt=0."""
    payload = {
        "repo": "x/y",
        "pipeline": "qa",
        "branch": "main",
        "qa": {"qa_timeout_seconds": 0},
    }
    r = await api_client.post("/api/v1/jobs", json=payload)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_post_jobs_qa_rejects_oversize_timeout(api_client) -> None:
    """qa_timeout_seconds must be <= 86400 (1 day)."""
    payload = {
        "repo": "x/y",
        "pipeline": "qa",
        "branch": "main",
        "qa": {"qa_timeout_seconds": 86401},
    }
    r = await api_client.post("/api/v1/jobs", json=payload)
    assert r.status_code == 422
