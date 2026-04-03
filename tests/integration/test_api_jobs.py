"""Integration tests for Jobs API — real PostgreSQL."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_and_get_job(app: AsyncClient):
    resp = await app.post(
        "/api/v1/jobs",
        json={"repo": "owner/repo", "task": "Fix the auth bug"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 201
    job = resp.json()
    assert job["job_id"].startswith("job-")
    assert job["status"] == "pending"

    resp = await app.get(f"/api/v1/jobs/{job['job_id']}")
    assert resp.status_code == 200
    assert resp.json()["task"] == "Fix the auth bug"


@pytest.mark.asyncio
async def test_list_jobs_with_filter(app: AsyncClient):
    await app.post("/api/v1/jobs", json={"repo": "a/b", "task": "Task 1"}, headers={"X-Requested-With": "XMLHttpRequest"})
    await app.post("/api/v1/jobs", json={"repo": "a/b", "task": "Task 2"}, headers={"X-Requested-With": "XMLHttpRequest"})
    await app.post("/api/v1/jobs", json={"repo": "c/d", "task": "Task 3"}, headers={"X-Requested-With": "XMLHttpRequest"})

    resp = await app.get("/api/v1/jobs", params={"repo": "a/b"})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 2


@pytest.mark.asyncio
async def test_cancel_job(app: AsyncClient):
    resp = await app.post("/api/v1/jobs", json={"repo": "owner/repo", "task": "Cancel me"}, headers={"X-Requested-With": "XMLHttpRequest"})
    job_id = resp.json()["job_id"]

    resp = await app.post(f"/api/v1/jobs/{job_id}/cancel", headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    resp = await app.get(f"/api/v1/jobs/{job_id}")
    assert resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_create_job_invalid_repo(app: AsyncClient):
    resp = await app.post("/api/v1/jobs", json={"repo": "invalid", "task": "Fix"}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_nonexistent_job(app: AsyncClient):
    resp = await app.get("/api/v1/jobs/job-nonexistent")
    assert resp.status_code == 404
