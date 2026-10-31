"""The game layer turned off through the settings document's ``ui`` section (#177), end to end on a real database.

- Nothing written: ``GET /config/ui`` answers the game on with the guild as
  home, and the game's routes answer as before.
- ``ui.game: false``: the game's reads (``GET /world``, a character, a
  quest's scroll, ``GET /progression``, ``POST /progression/rebuild``)
  answer 404 saying the game is off, while reviewing a job, at
  ``/jobs/{job_id}/…`` and at its deprecated aliases, and the content pack's
  routes work as before.
- A change applies on the next request, with no restart.
- Exactly the game's reads are gated, so a new route cannot join or leave
  the game without a decision.
- The real lifespan's app gates on the stored document, not on defaults.

The section is reached through the API, so each test fails with its own
message on a tree that has no ui section.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.dependencies.models import Dependant
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app, lifespan
from embry0.api.deps import bust_api_key_cache
from embry0.api.v1 import world as world_api
from embry0.config import Embry0Config
from embry0.services.progression_pack_store import PackStore
from embry0.services.world import WorldCache
from embry0.settings.document import load_yaml
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.organizations import OrganizationsRepository
from embry0.storage.repositories.progression_packs import ProgressionPacksRepository
from tests.fixtures.routes import app_routes
from tests.unit.api.v1.test_world_api import FakeProgression, FakeRepo
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture

pytestmark = pytest.mark.requires_postgres

SETTINGS = "/api/v1/config/settings"
UI = "/api/v1/config/ui"
ADMIN = "emb_" + "a" * 43
READER = "emb_" + "r" * 43
CSRF = {"X-Requested-With": "XMLHttpRequest"}
#: What a route of the game says while the game is off, so a client tells it from a missing record.
GAME_OFF = "The game layer is off: ui.game is false in the settings document."

#: The game's reads: each answers 404 while the game is off.
GAME_READS: list[tuple[str, str]] = [
    ("GET", "/api/v1/world"),
    ("GET", "/api/v1/world/characters/developer"),
    ("GET", "/api/v1/world/quests/job-1"),
    ("GET", "/api/v1/progression"),
    ("POST", "/api/v1/progression/rebuild"),
]


class Quests(FakeRepo):
    """The world's store, with the review's evaluation write."""

    def __init__(self) -> None:
        super().__init__()
        self.rated: list[dict[str, Any]] = []

    async def rate(self, job_id: str, *, rated_by: str, **fields: Any) -> dict[str, Any]:
        self.rated.append({"job_id": job_id, **fields})
        return {"rated_by": rated_by, **{k: v for k, v in fields.items() if v is not None}}


def _key(token: str) -> ApiKeyRow | None:
    scope = {ADMIN: "admin", READER: "read"}.get(token)
    if scope is None:
        return None
    return ApiKeyRow(
        id=f"key-{scope}0000001",
        user_id="usr-admin",
        name=f"{scope}-key",
        prefix="emb_xxxxxxxx",
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=None,
    )


@pytest.fixture
async def app(db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
    app = create_app(config)
    app.state.db = db_with_migrations
    app.state.settings = SettingsStore(db_with_migrations)
    app.state.world_repo = Quests()
    app.state.world_cache = WorldCache()
    app.state.progression = FakeProgression()
    app.state.progression_packs = PackStore(ProgressionPacksRepository(db_with_migrations))
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(
        side_effect=lambda h: next((_key(t) for t in (ADMIN, READER) if hash_token(t) == h), None)
    )
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    user = MagicMock(disabled=False, email="alex@example.com")
    app.state.users_repo = MagicMock(get=AsyncMock(side_effect=lambda uid: user if uid == "usr-admin" else None))

    async def leads(request: Any) -> list[dict[str, Any]]:
        return []

    async def regions(request: Any) -> dict[str, Any]:
        return {"regions": [], "frontier": []}

    async def no_pr_stats(request: Any, pr_url: str | None) -> None:
        return None

    monkeypatch.setattr(world_api, "_leads", leads)
    monkeypatch.setattr(world_api, "_regions", regions)
    monkeypatch.setattr(world_api, "_pr_stats", no_pr_stats)
    yield app
    bust_api_key_cache()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    headers = {"Authorization": f"Bearer {ADMIN}", **CSRF}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as c:
        yield c


async def _put(client: AsyncClient, text: str) -> Any:
    return await client.put(SETTINGS, content=text, headers={"Content-Type": "application/yaml"})


async def test_nothing_written_keeps_the_game_on_with_the_guild_as_home(client: AsyncClient) -> None:
    resp = await client.get(UI)
    assert resp.status_code == 200, f"GET /config/ui answers the dashboard's settings in effect: {resp.text}"
    assert resp.json() == {"game": True, "home": "guild"}, "an existing deployment keeps the game, on the guild"
    assert (await client.get("/api/v1/world")).status_code == 200, "and the guild's read answers as before"
    assert (await client.get("/api/v1/progression")).status_code == 200


async def test_any_signed_in_caller_may_read_it_and_no_one_else(app: Any) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        assert (await anonymous.get(UI)).status_code == 401, "it is not public: a sign-in comes first"
        reader = await anonymous.get(UI, headers={"Authorization": f"Bearer {READER}"})
    assert reader.status_code == 200 and reader.json()["game"] is True, f"a read key may read it: {reader.text}"


async def test_with_the_game_off_its_reads_answer_404_and_review_works(app: Any, client: AsyncClient) -> None:
    saved = await _put(client, "ui:\n  game: false\n")
    assert saved.status_code == 200, f"the ui section takes game: false: {saved.text}"
    assert (await client.get(UI)).json() == {"game": False, "home": "guild"}

    for method, path in GAME_READS:
        resp = await client.request(method, path)
        assert resp.status_code == 404, f"{method} {path} is the game's: 404 while it is off, got {resp.status_code}"
        assert resp.json()["detail"] == GAME_OFF, (
            f"{method} {path} says why, so a client can tell it from a missing record: {resp.text}"
        )

    for path in ("/api/v1/jobs/job-1/evaluation", "/api/v1/world/quests/job-1/evaluation"):
        rated = await client.put(path, json={"rating": "good", "note": "clean"})
        assert rated.status_code == 200, f"reviewing a job is core work, never the game's: PUT {path} {rated.text}"
    assert [r["rating"] for r in app.state.world_repo.rated] == ["good", "good"]

    pack = await client.get("/api/v1/progression/pack")
    assert pack.status_code == 200, f"the content pack is the game's configuration, kept while it is off: {pack.text}"


async def test_the_lead_kind_catalogue_answers_any_signed_in_caller_with_the_game_off(
    app: Any, client: AsyncClient
) -> None:
    """GET /lead-kinds (#166) is not the game's: with the game off the dashboard still names each kind of
    work on offer, in plain words, and serves no creature, leader, verb or level."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anonymous:
        reader = await anonymous.get("/api/v1/lead-kinds", headers={"Authorization": f"Bearer {READER}"})
        assert reader.status_code == 200, f"any signed-in caller may read the catalogue: {reader.text}"
        assert (await anonymous.get("/api/v1/lead-kinds")).status_code == 401, "it is not public"
    assert (await _put(client, "ui:\n  game: false\n")).status_code == 200
    off = await client.get("/api/v1/lead-kinds")
    assert off.status_code == 200, f"the catalogue answers with the game off: {off.status_code} {off.text}"
    assert off.json() == reader.json(), "the same plain names, the game on or off"
    game_words = sorted({key for kind in off.json()["kinds"] for key in kind} & {"verb", "creature", "leader", "level"})
    assert game_words == [], f"creatures and leaders are the game's, served with each quest: {game_words}"
    assert {k["id"]: k["label"] for k in off.json()["kinds"]}["weed"] == "Weed"


async def test_a_change_applies_on_the_next_request_with_no_restart(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/world")).status_code == 200
    assert (await _put(client, "ui:\n  game: off\n  home: overview\n")).status_code == 200
    assert (await client.get("/api/v1/world")).status_code == 404, "off on the very next request"
    assert (await client.get(UI)).json() == {"game": False, "home": "overview"}
    assert (await _put(client, "ui:\n  home: overview\n")).status_code == 200
    assert (await client.get("/api/v1/world")).status_code == 200, "and on again, with no restart"
    assert (await client.get(UI)).json() == {"game": True, "home": "overview"}


async def test_an_organization_cannot_turn_the_game_off(app: Any, client: AsyncClient) -> None:
    app.state.organizations_repo = OrganizationsRepository(app.state.db)
    await app.state.organizations_repo.register("acme", "github")
    resp = await client.put(
        SETTINGS,
        params={"owner": "acme"},
        content="ui:\n  game: false\n",
        headers={"Content-Type": "application/yaml", "If-Match": '"0"'},
    )
    assert resp.status_code == 422, f"the game layer is the deployment's: {resp.text}"
    assert [(p["loc"], p["type"]) for p in resp.json()["detail"]] == [(["ui", "game"], "not_overridable")]


def _calls(dependant: Dependant | None) -> set[Any]:
    found: set[Any] = set()
    for dep in dependant.dependencies if dependant is not None else ():
        found.add(dep.call)
        found |= _calls(dep)
    return found


def test_exactly_the_games_reads_are_gated() -> None:
    from embry0.api.v1.ui import require_game

    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    gated = sorted(
        (method, route.path)
        for route in app_routes(app)
        if require_game in _calls(route.dependant)
        for method in sorted(route.methods or ())
    )
    assert gated == [
        ("GET", "/api/v1/progression"),
        ("GET", "/api/v1/world"),
        ("GET", "/api/v1/world/characters/{agent_type}"),
        ("GET", "/api/v1/world/quests/{job_id}"),
        ("POST", "/api/v1/progression/rebuild"),
    ], f"only the game's own reads answer 404 while it is off; review and the pack's routes never do: {gated}"


@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespans_app_gates_on_the_stored_document(db_with_migrations: DatabasePool) -> None:
    """Without a store the gate reads the defaults, so the real app must be built with one."""
    config = Embry0Config(
        _env_file=None,
        database_url=db_with_migrations._url,  # noqa: SLF001
        auth_dev_mode=True,
        webhook_dev_mode=True,
        api_key="",
        watcher_enabled=False,
        volume_reaper_enabled=False,
        linear_reconcile_mode="off",
        telegram_bot_token="",
        xai_proxy_enabled=False,
        auth_proxy_enabled=False,
        context_proxy_enabled=False,
    )
    app = create_app(config)
    async with lifespan(app):
        text = "ui:\n  game: false\n"
        await app.state.settings.save(text, load_yaml(text), actor="alex@example.com")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get(UI)).json()["game"] is False, "the real app reads the stored document"
            resp = await client.get("/api/v1/world")
    assert resp.status_code == 404, f"and the real app's guild answers 404 with the game off: {resp.text}"
    assert resp.json()["detail"] == GAME_OFF, f"the 404 says the game is off, not that a record is missing: {resp.text}"
