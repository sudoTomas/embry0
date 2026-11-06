"""The world draws an area's dungeon from its repository's ``slop_policy.dungeon_min_level`` (#169)."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from embry0.api.v1 import world as world_api
from embry0.settings.sections import CATALOG
from embry0.settings.store import build_snapshot
from tests.unit.api.v1.test_world_api import NOW, _client, app  # noqa: F401 - app is a fixture

#: An area of acme's at Lasagna (level 3) with no open slop finding and no cleanup.
SEARCH = {
    "repo": "acme/web",
    "area": "Search",
    "score": 0.52,
    "level": 3,
    "name": "Lasagna",
    "provisional": False,
    "computed_at": NOW,
}


class _Meters:
    async def scores(self, repo: str | None = None) -> list[dict[str, Any]]:
        return [SEARCH]


class _Settings:
    def __init__(self, level: Any) -> None:
        self.rows = [
            {"scope": "deployment", "data": {}, "generation": 1, "updated_at": None},
            {"scope": "organization:acme", "data": {"slop_policy": {"dungeon_min_level": level}}, "generation": 1},
        ]

    async def snapshot(self) -> Any:
        return build_snapshot(CATALOG, self.rows, token=(), log=False)


async def _areas(app: Any) -> set[tuple[str, str | None]]:  # noqa: F811 - the fixture's value
    client: AsyncClient
    async with await _client(app) as client:
        world = (await client.get("/api/v1/world")).json()
    return {(d["repo"], d["area"]) for d in world["dungeons"]}


@pytest.fixture
def no_leads(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none(request: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(world_api, "_leads", none)


@pytest.mark.usefixtures("no_leads")
async def test_an_area_has_a_dungeon_from_its_organization_s_level(app: Any) -> None:  # noqa: F811
    app.state.slop_meter_repo = _Meters()
    assert ("acme/web", "Search") in await _areas(app), "level 2 by default: Lasagna has a dungeon"
    app.state.settings = _Settings(4)
    assert ("acme/web", "Search") not in await _areas(app), "acme draws from Sloppy Joe"
    app.state.settings = _Settings(None)
    assert ("acme/web", "Search") not in await _areas(app), "acme's meter alone draws none"
    app.state.settings = _Settings(3)
    assert ("acme/web", "Search") in await _areas(app)
