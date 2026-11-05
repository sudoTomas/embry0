"""The guild draws its characters and parties from the agent and template rows (#167).

Before, a character could be given a task only when its type was one of the
built-in names the world knew, so an agent of the deployment's own never
could; its station and a template's leader were name lists too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import world as world_api
from embry0.config import Embry0Config
from embry0.services.progression import Character, Progression
from embry0.services.progression_pack import default_rules
from embry0.services.world import WorldCache
from tests.unit.api.v1.test_world_api import FakeProgression, FakeRepo

NOW = datetime.now(UTC)


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.reads = 0

    async def list_all(self) -> list[dict[str, Any]]:
        self.reads += 1
        return [dict(r) for r in self.rows]


class _Characters(FakeProgression):
    async def read(self) -> Progression:
        rules = default_rules()
        types = ("developer", "qa", "scout", "watchman", "lost")
        return Progression(characters={a: Character(a, rules=rules) for a in types}, rules=rules)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Any:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    app.state.db = None
    app.state.world_repo = FakeRepo()
    app.state.world_cache = WorldCache()
    app.state.progression = _Characters()

    async def nothing(request: Any) -> list[dict[str, Any]]:
        return []

    async def no_places(request: Any) -> dict[str, Any]:
        return {"regions": [], "frontier": []}

    monkeypatch.setattr(world_api, "_leads", nothing)
    monkeypatch.setattr(world_api, "_regions", no_places)
    app.state.agent_defs_repo = _Rows(
        [
            {"type": "developer", "station": "anvil", "quest_template": ""},
            {"type": "qa", "station": "alchemy_table", "quest_template": None},
            # Agents of the deployment's own: one the guild can send, one it cannot, one whose template is gone.
            {"type": "scout", "station": "lectern", "quest_template": "scout-pass"},
            {"type": "watchman", "station": "desk", "quest_template": None},
            {"type": "lost", "station": "map_table", "quest_template": "deleted-route"},
        ]
    )
    app.state.templates_repo = _Rows(
        [
            {"name": "scout-pass", "leader_agent": "scout", "fixed_route": True, "offered_to_triage": False},
            {"name": "full-magnum-opus", "default_for_kind": "code"},
        ]
    )
    return app


async def _world(app: Any) -> dict[str, Any]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/world")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_an_agent_of_the_deployments_own_with_a_quest_template_can_be_sent_from_the_guild(app: Any) -> None:
    characters = {c["agent_type"]: c for c in (await _world(app))["characters"]}
    scout = characters["scout"]
    assert scout["can_quest"] is True, f"the guild cannot send an agent whose row names its task: {scout}"
    assert scout["quest_template"] == "scout-pass", "its task runs the template its row names"
    assert scout["station"] == "lectern", "it works at the station its row names"
    assert characters["watchman"]["can_quest"] is False, "a row naming no task gets none"
    assert characters["lost"]["can_quest"] is False, "a task naming a template that is gone is none"
    developer = characters["developer"]
    assert (developer["can_quest"], developer["quest_template"], developer["station"]) == (True, None, "anvil"), (
        "the developer's task names no template, so triage picks its route"
    )
    assert characters["qa"]["can_quest"] is False and characters["qa"]["station"] == "alchemy_table"


async def test_a_party_with_no_agent_out_yet_is_drawn_as_its_templates_leader(app: Any) -> None:
    async def jobs(**_kw: Any) -> list[dict[str, Any]]:
        return [
            {
                "job_id": "job-new",
                "repo": "acme/web",
                "task": "Scout it",
                "status": "pending",
                "pipeline_template": "scout-pass",
                "created_at": NOW,
            }
        ]

    async def no_runs(ids: list[str]) -> dict[str, list[tuple[str, str]]]:
        return {}

    app.state.world_repo.jobs = jobs
    app.state.world_repo.agent_runs = no_runs
    party = (await _world(app))["parties"][0]
    assert party["leader"] == "scout", f"the template's row names its leader: {party}"


async def test_a_write_to_the_rows_reaches_the_hall_on_its_next_read(app: Any) -> None:
    from embry0.services.world import forget_rows

    await _world(app)
    await _world(app)
    assert app.state.agent_defs_repo.reads == 1, "the rows are kept between reads, like the board"
    app.state.agent_defs_repo.rows.append({"type": "lost", "station": "desk", "quest_template": "scout-pass"})
    forget_rows(app.state, "agents")
    await _world(app)
    assert app.state.agent_defs_repo.reads == 2, "a write forgets the kept rows"


def test_forgetting_rows_without_a_cache_does_nothing() -> None:
    from embry0.services.world import forget_rows

    class State:
        world_cache = None

    forget_rows(State(), "agents")
    state = State()
    state.world_cache = WorldCache()
    forget_rows(state, "templates")
