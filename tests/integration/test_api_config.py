"""Integration tests for Config API — real PostgreSQL."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_budget_get_defaults(app: AsyncClient):
    resp = await app.get("/api/v1/config/budget")
    assert resp.status_code == 200
    data = resp.json()
    assert data["max_budget_per_job_usd"] == 10.0
    assert data["overrun_mode"] == "soft"


@pytest.mark.asyncio
async def test_budget_update_and_get(app: AsyncClient):
    resp = await app.put(
        "/api/v1/config/budget",
        json={"max_budget_per_job_usd": 25.0, "overrun_mode": "hard"},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200

    resp = await app.get("/api/v1/config/budget")
    data = resp.json()
    assert data["max_budget_per_job_usd"] == 25.0
    assert data["overrun_mode"] == "hard"


@pytest.mark.asyncio
async def test_context_global_set_and_get(app: AsyncClient):
    resp = await app.put(
        "/api/v1/config/context",
        json={"system_context": "Use TypeScript strict.", "assistant_context": "Focus on tests."},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200

    resp = await app.get("/api/v1/config/context")
    data = resp.json()
    assert data["system_context"] == "Use TypeScript strict."


@pytest.mark.asyncio
async def test_context_repo_set_and_get(app: AsyncClient):
    resp = await app.put(
        "/api/v1/config/context/repos/owner/repo",
        json={"system_context": "Follow hexagonal architecture."},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 200

    resp = await app.get("/api/v1/config/context/repos/owner/repo")
    data = resp.json()
    assert data["system_context"] == "Follow hexagonal architecture."
