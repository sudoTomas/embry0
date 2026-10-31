"""The asks policy read per organization (spec 2026-10-29, section 11; #161), on a real database.

Each organization's layer sets when and how often asks about its decisions
go out. The ask loop here is built with the reader the real lifespan gives
the production ask loop, so these tests also pin that production reads
every ask by its decision's organization: a reader that read only the
deployment's values would look exactly like organizations that set nothing.
The layers are written through ``PUT /config/settings?owner=``.

The feed, ``GET /asks/due``, is ``tests/unit/api/v1/test_asks_api.py``; its
last test here runs it on the real store and the real asks table.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app, lifespan
from embry0.api.deps import bust_api_key_cache
from embry0.api.v1 import asks as asks_api
from embry0.config import Embry0Config
from embry0.services.ask_loop import AskLoop
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.asks import AsksRepository
from embry0.storage.repositories.organizations import OrganizationsRepository
from tests.fixtures.asks import CountingDb
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}
#: Tuesday 2026-10-27, 20:00 UTC: 05:00 Wednesday in Tokyo, 15:00 Tuesday in Chicago (CDT).
NOW = datetime(2026, 10, 27, 20, 0, tzinfo=UTC)
#: The deployment's next working hour after NOW: 09:00 UTC on Wednesday.
DEPLOYMENT_NEXT = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
DEPLOYMENT = "asks:\n  enabled: true\n  hours: 09-18\n  weekdays: mon-fri\n  timezone: UTC\n"


@pytest.fixture
async def app(db_with_migrations: DatabasePool, stub_container_runtime: Any) -> AsyncIterator[Any]:  # noqa: F811
    """The real app through its real lifespan, with ``acme`` and ``globex`` registered."""
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
        for owner in ("acme", "globex"):
            await app.state.organizations_repo.register(owner, "github")
        yield app


async def _put(app: Any, text: str, **params: str) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS) as client:
        resp = await client.put(URL, content=text, headers={"Content-Type": "application/yaml"}, params=params)
    assert resp.status_code == 200, f"the settings document took {text!r} at {params or 'the deployment'}: {resp.text}"


class FakeAsks:
    def __init__(self, rows: Iterable[dict[str, Any]] = ()) -> None:
        self.rows = list(rows)

    async def live(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows if r["state"] in ("queued", "sent", "reminded")]

    async def expired_pairs(self) -> set[tuple[str, str]]:
        return {(r["decision_id"], r["recipient"]) for r in self.rows if r["state"] == "expired"}

    async def code(self, decision_id: str) -> str:
        return "ABCD"

    async def add(self, *, decision_id: str, recipient: str, due_at: datetime) -> str | None:
        ask_id = f"a{len(self.rows) + 1}"
        row = {"id": ask_id, "decision_id": decision_id, "recipient": recipient, "state": "queued"}
        self.rows.append({**row, "due_at": due_at, "created_at": NOW})
        return ask_id

    async def queue(self, asks: Any) -> list[str]:
        """As the repository does: each decision gets its code, then each ask its person holds no live one of."""
        queued = []
        for decision_id, recipient, due_at in asks:
            await self.code(decision_id)
            if not any(r["decision_id"] == decision_id and r["recipient"] == recipient for r in await self.live()):
                queued.append(await self.add(decision_id=decision_id, recipient=recipient, due_at=due_at))
        return [q for q in queued if q]

    async def close(self, ask_ids: Any, *, state: str, now: datetime) -> int:
        for r in self.rows:
            if r["id"] in set(ask_ids):
                r["state"] = state
        return len(set(ask_ids))

    def due(self) -> dict[str, datetime]:
        """Each queued ask's decision to when it is due."""
        return {r["decision_id"]: r["due_at"] for r in self.rows if r["state"] == "queued"}

    def state(self, decision_id: str) -> str:
        return next(str(r["state"]) for r in self.rows if r["decision_id"] == decision_id)


def _loop(app: Any, asks: FakeAsks, decisions: Callable[[], list[dict[str, Any]]]) -> AskLoop:
    """An ask loop on the production ask loop's own settings reader, with the rest faked."""

    async def listed() -> list[dict[str, Any]]:
        return decisions()

    async def people() -> dict[str, str]:
        return {"ada": "ada@example.com"}

    return AskLoop(settings=app.state.ask_loop.settings, asks=asks, decisions=listed, people=people, clock=lambda: NOW)


def _decision(decision_id: str, repo: str) -> dict[str, Any]:
    return {"id": decision_id, "repo": repo, "owners": ["ada"]}


async def test_two_organizations_each_get_asks_within_their_own_working_time_in_one_tick(app: Any) -> None:
    await _put(app, DEPLOYMENT)
    await _put(app, "asks:\n  hours: 10-19\n  timezone: Asia/Tokyo\n", owner="acme")
    await _put(app, "asks:\n  hours: 08-17\n  timezone: America/Chicago\n", owner="globex")
    decisions = [_decision("acme", "acme/widgets"), _decision("globex", "globex/gizmos"), _decision("none", "")]
    asks = FakeAsks()

    counts = await _loop(app, asks, lambda: decisions).tick()
    assert counts == {"expired": 0, "withdrawn": 0, "queued": 3}, (
        f"asks are on in the stored document, so one tick queues one ask per decision: {counts}"
    )
    due = asks.due()
    assert due["acme"] == datetime(2026, 10, 28, 1, 0, tzinfo=UTC), (
        f"acme's ask waits for acme's 10:00 in Tokyo (01:00 UTC on Wednesday), not the deployment's: {due['acme']}"
    )
    assert due["globex"] == NOW, f"15:00 in Chicago is within globex's 08-17, so its ask is due now: {due['globex']}"
    assert due["none"] == DEPLOYMENT_NEXT, (
        f"a decision with no repository has no organization: the deployment's 09:00 UTC: {due['none']}"
    )


async def test_an_ask_about_a_decision_with_no_repository_reads_the_deployment_s_values(app: Any) -> None:
    await _put(app, DEPLOYMENT + "  expire_days: 7\n")
    await _put(app, "asks:\n  hours: 00-24\n  weekdays: mon-sun\n  expire_days: 1\n", owner="acme")
    two_days_old = {"recipient": "ada@example.com", "state": "sent", "due_at": NOW - timedelta(days=2)}
    asks = FakeAsks(
        {"id": f"old-{d}", "decision_id": d, **two_days_old, "created_at": NOW - timedelta(days=2)}
        for d in ("acme", "none", "gone")
    )
    decisions = [_decision("acme", "acme/widgets"), _decision("none", ""), _decision("none-2", "")]

    counts = await _loop(app, asks, lambda: decisions).tick()
    assert asks.state("acme") == "expired", "acme's ask, two days old, expires by acme's expire_days 1"
    assert asks.state("none") == "sent", (
        "the ask about a decision with no repository is held to the deployment's expire_days 7, not acme's 1"
    )
    assert asks.state("gone") == "withdrawn", "an ask whose decision is no longer open is withdrawn, never expired"
    assert asks.due()["none-2"] == DEPLOYMENT_NEXT, (
        "a new ask about a decision with no repository waits for the deployment's working hours, not acme's 00-24"
    )
    assert counts == {"expired": 1, "withdrawn": 1, "queued": 1}, f"one tick did all of it: {counts}"


async def test_a_layer_change_applies_on_the_next_tick_with_no_restart(app: Any) -> None:
    await _put(app, DEPLOYMENT)
    decisions = [_decision("acme-1", "acme/widgets")]
    asks = FakeAsks()
    loop = _loop(app, asks, lambda: decisions)

    await loop.tick()
    assert asks.due() == {"acme-1": DEPLOYMENT_NEXT}, "no layer yet: acme reads the deployment's hours"

    await _put(app, "asks:\n  hours: 00-24\n  weekdays: mon-sun\n", owner="acme")
    decisions += [_decision("acme-2", "acme/widgets"), _decision("globex-1", "globex/gizmos")]
    await loop.tick()
    due = asks.due()
    assert due["acme-2"] == NOW, f"acme's layer reached the same loop's next tick: its ask is due now: {due['acme-2']}"
    assert due["globex-1"] == DEPLOYMENT_NEXT, "globex has no layer and still reads the deployment's hours"

    await _put(app, "", owner="acme")
    decisions.append(_decision("acme-3", "acme/widgets"))
    await loop.tick()
    assert asks.due()["acme-3"] == DEPLOYMENT_NEXT, "acme's layer cleared: the next tick reads the deployment's again"


async def test_the_production_loop_reads_the_repository_of_a_real_decision(app: Any) -> None:
    """The decisions, the people and the settings reader are all the production ask loop's own.

    Every other loop test fakes the decisions, so nothing else would notice the
    decisions list stop naming a decision's repository: each ask would then
    read the deployment's values, as if no organization set anything.
    """
    await _put(app, DEPLOYMENT)
    await _put(app, "asks:\n  hours: 00-24\n  weekdays: mon-sun\n", owner="acme")
    await app.state.repo_preferences_repo.upsert("acme/widgets", owners=["ada"])
    await app.state.issues_repo.create("Widgets break on resize", repo="acme/widgets")
    production = app.state.ask_loop
    asks = FakeAsks()
    loop = AskLoop(
        settings=production.settings,
        asks=asks,
        decisions=production.decisions,
        people=production.people,
        clock=lambda: NOW,
    )

    counts = await loop.tick()
    assert counts["queued"] == 1, f"the open issue in acme/widgets is a decision its owner ada is asked about: {counts}"
    assert list(asks.due().values()) == [NOW], (
        "the decision names its repository, acme/widgets, so its ask reads acme's always-open hours and is due now, "
        f"not at the deployment's 09:00 UTC: {asks.due()}"
    )


ADMIN = "emb_" + "a" * 43


@pytest.fixture
async def feed(db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """The feed on the real settings store and the real asks table, with an admin key; decisions faked."""
    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
    app = create_app(config)
    app.state.db = db_with_migrations
    app.state.settings = SettingsStore(db_with_migrations)
    app.state.asks_repo = AsksRepository(db_with_migrations)
    app.state.organizations_repo = OrganizationsRepository(db_with_migrations)
    for owner in ("acme", "globex"):
        await app.state.organizations_repo.register(owner, "github")
    row = ApiKeyRow(
        id="key-admin0000001",
        user_id="usr-admin",
        name="admin-key",
        prefix="emb_xxxxxxxx",
        scope="admin",
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=None,
    )
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: row if h == hash_token(ADMIN) else None)
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    user = MagicMock(disabled=False, email="alex@example.com")
    app.state.users_repo = MagicMock(
        get=AsyncMock(side_effect=lambda uid: user if uid == "usr-admin" else None),
        get_by_email=AsyncMock(return_value=None),
    )
    decisions = [
        {"id": "acme-1", "repo": "acme/widgets", "stage": "offered", "owners": ["ada"], "actions": []},
        {"id": "acme-2", "repo": "acme/widgets", "stage": "offered", "owners": ["ada"], "actions": []},
        {"id": "globex-1", "repo": "globex/gizmos", "stage": "offered", "owners": ["ada"], "actions": []},
    ]

    async def open_now(_request: Any) -> list[dict[str, Any]]:
        return [dict(d) for d in decisions]

    monkeypatch.setattr(asks_api, "open_decisions", open_now)
    yield app
    bust_api_key_cache()


async def test_the_feed_reads_each_organization_s_layer_from_the_real_store(feed: Any) -> None:
    headers = {"Authorization": f"Bearer {ADMIN}", **HEADERS, "Content-Type": "application/yaml"}
    async with AsyncClient(transport=ASGITransport(app=feed), base_url="http://test", headers=headers) as client:
        for text, params in (
            ("asks:\n  enabled: true\n  hours: 00-24\n  weekdays: mon-sun\n  max_open_per_person: 1\n", {}),
            ("asks:\n  max_open_per_person: 2\n", {"owner": "acme"}),
        ):
            put = await client.put(URL, content=text, params=params)
            assert put.status_code == 200, put.text
        asks: AsksRepository = feed.state.asks_repo
        now = datetime.now(UTC)
        [held] = await asks.queue([("acme-1", "ada@example.com", now - timedelta(hours=1))])
        assert await asks.sent(held, message_ref="m-1", now=now) is not None
        await asks.queue((d, "ada@example.com", now - timedelta(minutes=1)) for d in ("acme-2", "globex-1"))
        due = (await client.get("/api/v1/asks/due")).json()["asks"]
    assert sorted(a["decision"]["id"] for a in due) == ["acme-2", "globex-1"], (
        "ada holds one acme ask: acme's layer lets her hold two, and globex's count starts at none, "
        f"so both go out although the deployment's limit is one: {[a['decision']['id'] for a in due]}"
    )


def _code_lookups(statements: list[str]) -> tuple[int, int]:
    """How many statements asked which codes are taken, one per code length a batch needed, and how many others."""
    lookups = sum(s.startswith("SELECT code FROM decision_codes WHERE code = ANY") for s in statements)
    return lookups, len(statements) - lookups


async def test_the_production_loop_keeps_the_ask_of_a_decision_ranked_past_500(app: Any) -> None:
    """#273: the decisions list shows at most 500, but the loop must read every open decision.

    Ranked newest first, the 501st open issue is the oldest. A loop that read
    only the first 500 never asked about it, and withdrew a live ask the moment
    a newer decision pushed it past 500. The decisions, the asks table and the
    settings reader are all the production loop's own.
    """
    await _put(app, DEPLOYMENT)
    await app.state.repo_preferences_repo.upsert("acme/widgets", owners=["ada"])
    for i in range(500):
        await app.state.issues_repo.create(f"Widgets break, case {i}", repo="acme/widgets")
    production = app.state.ask_loop
    counting = CountingDb(app.state.db)
    loop = AskLoop(
        settings=production.settings,
        asks=AsksRepository(counting),  # type: ignore[arg-type]
        decisions=production.decisions,
        people=production.people,
        clock=lambda: NOW,
    )

    first = await loop.tick()
    assert first["queued"] == 500, f"one ask for each of the 500 open issues: {first}"
    first_lookups, first_rest = _code_lookups(counting.statements)
    longest = max(
        len(c) for c in (await production.asks.codes(a["decision_id"] for a in await production.asks.live())).values()
    )
    assert first_lookups == longest - 3, (
        f"the codes of 500 new decisions were asked one length at a time, four letters to {longest}: "
        f"{first_lookups} statements"
    )

    await app.state.issues_repo.create("Widgets break, the newest case", repo="acme/widgets")
    counting.statements.clear()
    second = await loop.tick()
    assert second == {"expired": 0, "withdrawn": 0, "queued": 1}, (
        "a 501st open decision ranks first, which pushes the oldest past 500: its ask stays, and only the newest "
        f"is asked about: {second}"
    )
    second_lookups, second_rest = _code_lookups(counting.statements)
    assert second_rest == first_rest and second_lookups >= 1, (
        f"apart from asking codes one length at a time, the asks table took as many statements to queue one ask "
        f"as to queue 500 ({second_rest} against {first_rest}): a tick makes no round trip per decision"
    )
    live = await production.asks.live()
    assert len(live) == 501, f"one live ask for each of the 501 open decisions: {len(live)}"
