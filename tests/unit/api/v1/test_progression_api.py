"""GET /progression answers the service's computation, and says so when the service is not there."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from embry0.api.v1 import progression as progression_api
from embry0.services.progression import compute
from embry0.storage.repositories.progression import ProgressEvent


class FakeService:
    async def read(self) -> Any:
        return compute([ProgressEvent("merged", "developer", datetime(2026, 10, 1, tzinfo=UTC))], ["developer"])


def _app(service: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(progression_api.router, prefix="/api/v1")
    app.state.progression = service
    return app


@pytest.mark.asyncio
async def test_the_read_answers_skills_total_level_agents_and_recent_level_ups() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app(FakeService())), base_url="http://test") as client:
        resp = await client.get("/api/v1/progression")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"total_level", "total_xp", "stats", "agents", "recent", "computed_at"}
    assert body["total_xp"] == 90_000 and len(body["stats"]) == 21
    assert body["agents"][0]["agent_type"] == "developer" and body["agents"][0]["stat"] == "smithing"
    assert body["recent"][0]["kind"] == "level" and body["recent"][0]["agent_type"] == "developer"


@pytest.mark.asyncio
async def test_a_missing_service_answers_503() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app(None)), base_url="http://test") as client:
        resp = await client.get("/api/v1/progression")
    assert resp.status_code == 503
