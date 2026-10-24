"""GET /progression answers the service's computation, and says so when the service is not there."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from embry0.api.v1 import progression as progression_api
from embry0.services.achievements import evaluate
from embry0.services.progression import compute
from embry0.storage.repositories.progression import ProgressEvent


class FakeService:
    async def read(self) -> Any:
        events = [ProgressEvent("merged", "developer", datetime(2026, 10, 1, tzinfo=UTC))]
        progression = compute(events, ["developer"])
        progression.achievements, progression.unlocks = evaluate(events, [], progression.level_ups)
        return progression


def _app(service: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(progression_api.router, prefix="/api/v1")
    app.state.progression = service
    return app


@pytest.mark.asyncio
async def test_the_read_answers_the_account_its_characters_and_achievements() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app(FakeService())), base_url="http://test") as client:
        resp = await client.get("/api/v1/progression")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "account",
        "skills",
        "characters",
        "achievements",
        "quests",
        "diaries",
        "recent",
        "computed_at",
    }
    assert body["account"] == {
        "xp": 60_000,
        "total_level": body["characters"][0]["total_level"],
        "characters": 1,
        "quest_points": 0,
        "divine_trim": False,
    }
    assert len(body["skills"]) == 23 and body["skills"][0] == {
        "id": "coding",
        "name": "Coding",
        "group": "build",
        "echo": "smithing",
    }
    developer = body["characters"][0]
    assert developer["agent_type"] == "developer" and developer["best_skill"] == "coding"
    assert len(developer["skills"]) == 23
    assert {r["kind"] for r in body["recent"]} == {"level", "achievement"}
    assert len(body["achievements"]) == 14 and body["achievements"][1]["id"] == "forged_in_fire"


@pytest.mark.asyncio
async def test_a_missing_service_answers_503() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app(None)), base_url="http://test") as client:
        resp = await client.get("/api/v1/progression")
    assert resp.status_code == 503
