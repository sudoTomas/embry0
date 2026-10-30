"""GET /repos/{repo}/-/slop-meter (spec 2026-10-26, slop-o-meter): a repository's score, its areas' and a weekly series."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.config import Embry0Config

AT = datetime(2026, 10, 26, 6, tzinfo=UTC)


class Registry:
    async def registered_as(self, repo: str) -> str | None:
        return "Acme/Web" if repo.lower() == "acme/web" else None


class Store:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    async def scores(self, repo: str | None = None) -> list[dict[str, Any]]:
        row = {"repo": repo, "provisional": False, "computed_at": AT}
        return [
            {**row, "area": "", "score": 0.3, "level": 2, "name": "Spaghetti Bolognese"},
            {**row, "area": "Billing", "score": 0.7, "level": 4, "name": "Sloppy Joe", "provisional": True},
        ]

    async def state(self, repo: str) -> dict[str, Any]:
        return {"repo": repo, "commits_read": 120, "commits_total": 120, "computed_at": AT, "error": None}

    async def weeks(self, repo: str, area: str = "") -> list[dict[str, Any]]:
        self.asked.append((repo, area))
        return [
            {
                "week": date(2026, 10, 12),
                "cost": 500.123,
                "capacity": 400.0,
                "slop": 100.123,
                "lines": 9000.0,
                "score": 0.2,
            },
            {"week": date(2026, 10, 19), "cost": 100.0, "capacity": 600.0, "slop": 0.0, "lines": 9100.0, "score": 0.18},
        ]


@pytest.fixture
def app() -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.db = None
    app.state.repos_repo = Registry()
    app.state.slop_meter_repo = Store()
    return app


async def _get(app: Any, path: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


async def test_the_repository_page_reads_its_score_areas_and_weeks(app: Any) -> None:
    resp = await _get(app, "/api/v1/repos/acme/web/-/slop-meter")
    assert resp.status_code == 200
    body = resp.json()
    assert body["repo"] == "Acme/Web" and body["area"] is None
    assert body["meter"] == {
        "score": 0.3,
        "level": 2,
        "name": "Spaghetti Bolognese",
        "provisional": False,
        "computed_at": AT.isoformat(),
    }
    assert [a["area"] for a in body["areas"]] == ["Billing"] and body["areas"][0]["meter"]["provisional"] is True
    assert body["weeks"][0] == {
        "week": "2026-10-12",
        "cost": 500.12,
        "capacity": 400.0,
        "slop": 100.12,
        "lines": 9000.0,
        "score": 0.2,
    }
    assert body["reading"] == {"commits_read": 120, "commits_total": 120, "computed_at": AT.isoformat(), "error": None}


async def test_an_area_series_and_the_refusals(app: Any) -> None:
    ok = await _get(app, "/api/v1/repos/acme/web/-/slop-meter?area=Billing")
    assert ok.status_code == 200 and ok.json()["area"] == "Billing"
    assert app.state.slop_meter_repo.asked[-1] == ("Acme/Web", "Billing")
    assert (await _get(app, "/api/v1/repos/acme/web/-/slop-meter?area=Nope")).status_code == 404
    assert (await _get(app, "/api/v1/repos/acme/gone/-/slop-meter")).status_code == 404
