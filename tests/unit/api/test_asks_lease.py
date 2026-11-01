"""A hand-out's lease (#293), on a real database.

``GET /asks/due`` leases each ask it hands out for ``asks.lease_minutes``.
While the lease runs the ask counts toward its person's limit and goes to no
other poll; once it lapses unreported, the same ask is handed out again
before any other of its person. Before #293 an ask handed out counted toward
nothing until it was reported sent, so an assistant that polled again while
it was still sending was handed a fresh ask past each one, and two polls at
once could each hand out the same ask, or between them more of one person's
asks than the limit.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.deps import bust_api_key_cache
from embry0.api.v1 import asks as asks_api
from embry0.config import Embry0Config
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.asks import AsksRepository
from embry0.storage.repositories.organizations import OrganizationsRepository
from tests.unit.api.test_asks_never_sent import _due, _issue, _loop_at, _shift_feed
from tests.unit.api.test_asks_per_organization import DEPLOYMENT, _put, app  # noqa: F401
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture

pytestmark = pytest.mark.requires_postgres

HEADERS = {"X-Requested-With": "XMLHttpRequest"}
ALWAYS = DEPLOYMENT.replace("09-18", "00-24").replace("mon-fri", "mon-sun")
#: Past the default lease of 15 minutes.
LAPSED = timedelta(minutes=16)


def _client(app: Any) -> AsyncClient:  # noqa: F811 - the app, not the fixture
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS)


async def _ada_owns(app: Any, quests: int) -> None:  # noqa: F811 - the app, not the fixture
    """ada owns ``quests`` open quests in acme/widgets and may hold three asks, at any hour."""
    await _put(app, ALWAYS)
    await app.state.repo_preferences_repo.upsert("acme/widgets", owners=["ada"])
    for k in range(quests):
        await _issue(app.state.db, f"iss-{k}", "acme/widgets", 100 - k)


async def test_three_polls_before_any_report_hand_out_three_asks_not_nine(
    app: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reviewer's probe: three polls a minute apart while the assistant is still sending, nothing reported."""
    await _ada_owns(app, quests=9)
    later = [timedelta(0)]
    _shift_feed(monkeypatch, later)
    await _loop_at(app, later).tick()
    pages: list[list[str]] = []
    async with _client(app) as client:
        for minute in range(3):
            later[0] = timedelta(minutes=minute)
            pages.append([a["id"] for a in await _due(client)])
    handed = {ask for page in pages for ask in page}
    assert len(handed) == 3, (
        f"ada may hold three asks, and none was reported sent: three polls handed her {len(handed)} distinct "
        f"asks: {[len(page) for page in pages]}"
    )
    assert pages[1:] == [[], []], f"the polls after the first hand out nothing while the leases run: {pages}"


async def test_a_lapsed_lease_hands_out_the_same_ask_again(
    app: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One of ada's three asks is reported sent; the other two are not. Once their leases lapse the next poll
    hands those two out again, not two fresh asks past them, and each keeps the time it was first handed out."""
    await _ada_owns(app, quests=6)
    later = [timedelta(0)]
    _shift_feed(monkeypatch, later)
    await _loop_at(app, later).tick()
    async with _client(app) as client:
        first = [a["id"] for a in await _due(client)]
        assert len(first) == 3, f"ada's first page: {first}"
        reported = await client.post(f"/api/v1/asks/{first[0]}/sent", json={"message_ref": "m"})
        assert reported.status_code == 200, reported.text
        stamped = {
            r["id"]: r["handed_out_at"]
            for r in await app.state.db.fetch("SELECT id, handed_out_at FROM asks WHERE handed_out_at IS NOT NULL")
        }
        later[0] = LAPSED
        again = [a["id"] for a in await _due(client)]
    assert sorted(again) == sorted(first[1:]), (
        f"the two asks handed out and never reported go out again, not fresh ones past them: {again} for {first[1:]}"
    )
    kept = {
        r["id"]: r["handed_out_at"]
        for r in await app.state.db.fetch("SELECT id, handed_out_at FROM asks WHERE handed_out_at IS NOT NULL")
    }
    assert kept == stamped, f"handing an ask out again keeps the time it was first handed out: {kept} != {stamped}"


async def test_a_peek_leases_nothing_and_shows_what_a_poll_would_get(
    app: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ada_owns(app, quests=5)
    later = [timedelta(0)]
    _shift_feed(monkeypatch, later)
    await _loop_at(app, later).tick()

    async def peek(client: AsyncClient) -> list[str]:
        return [a["id"] for a in (await client.get("/api/v1/asks/due", params={"peek": "1"})).json()["asks"]]

    async with _client(app) as client:
        looked = await peek(client)
        assert len(looked) == 3 and await peek(client) == looked, "a peek leases nothing: the next one sees the same"
        handed = [a["id"] for a in await _due(client)]
        assert handed == looked, f"the poll after the peeks hands out the page they showed: {handed} for {looked}"
        assert await peek(client) == [], "while their leases run, ada holds three: a peek shows a poll gets none"
        later[0] = LAPSED
        assert sorted(await peek(client)) == sorted(handed), "once they lapse a peek shows them again"
        assert sorted(a["id"] for a in await _due(client)) == sorted(handed), "and leased none: the poll gets them"


async def test_the_lease_runs_for_the_deployment_s_lease_minutes(
    app: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _ada_owns(app, quests=6)
    await _put(app, ALWAYS + "  lease_minutes: 30\n")
    later = [timedelta(0)]
    _shift_feed(monkeypatch, later)
    await _loop_at(app, later).tick()
    async with _client(app) as client:
        handed = sorted(a["id"] for a in await _due(client))
        later[0] = timedelta(minutes=29)
        assert await _due(client) == [], "29 minutes on, a lease of 30 still runs"
        later[0] = timedelta(minutes=31)
        assert sorted(a["id"] for a in await _due(client)) == handed, "31 minutes on it lapsed: the same asks again"


async def test_a_reminder_goes_to_one_poll_while_its_lease_runs(
    app: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ada's ask is reported sent; its reminder falls due six minutes on, while the first send's lease would still
    have run had the report not ended it. The reminder is leased like a first send."""
    await _ada_owns(app, quests=1)
    await _put(app, ALWAYS + "  remind_hours: 0.1\n")
    later = [timedelta(0)]
    _shift_feed(monkeypatch, later)
    await _loop_at(app, later).tick()
    async with _client(app) as client:
        (ask,) = await _due(client)
        assert (await client.post(f"/api/v1/asks/{ask['id']}/sent", json={"message_ref": "m"})).status_code == 200
        later[0] = timedelta(minutes=7)
        reminder = await _due(client)
        assert [(a["id"], a["reminder"]) for a in reminder] == [(ask["id"], True)], (
            f"the report ended the hand-out's lease: the reminder due after six minutes goes out: {reminder}"
        )
        later[0] = timedelta(minutes=8)
        assert await _due(client) == [], "the next poll, while the reminder's lease runs, gets nothing"
        later[0] = timedelta(minutes=7) + LAPSED
        assert [a["reminder"] for a in await _due(client)] == [True], "once it lapses unreported it goes again"
        assert (await client.post(f"/api/v1/asks/{ask['id']}/sent", json={"message_ref": "r"})).status_code == 200
        later[0] = timedelta(minutes=7) + 2 * LAPSED
        assert await _due(client) == [], "reported, the reminder is done"


async def test_a_second_poll_s_report_of_the_same_send_is_not_the_reminder(
    app: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review's case: poll X takes ada's ask and is still sending when its lease lapses; poll Y takes it again.
    X reports it sent, then Y does. Y's report is the same message, not the reminder: the ask stays sent with no
    reminder counted, and the reminder still goes out a day later."""
    await _ada_owns(app, quests=1)
    later = [timedelta(0)]
    _shift_feed(monkeypatch, later)
    await _loop_at(app, later).tick()
    async with _client(app) as client:
        (x,) = await _due(client)
        later[0] = LAPSED
        (y,) = await _due(client)
        assert (y["id"], y["reminder"]) == (x["id"], False), f"Y takes the same first send again: {y}"
        assert (await client.post(f"/api/v1/asks/{x['id']}/sent", json={"message_ref": "from-x"})).status_code == 200
        second = await client.post(f"/api/v1/asks/{y['id']}/sent", json={"message_ref": "from-y"})
        assert second.status_code == 200, second.text
        row = await app.state.asks_repo.get(x["id"])
        assert (second.json()["state"], row["state"], row["reminders"]) == ("sent", "sent", 0), (
            f"Y's report, right after X's, is the same message, not the reminder: {second.json()['state']}, "
            f"stored {row['state']} with {row['reminders']} reminders"
        )
        later[0] = LAPSED + timedelta(hours=24, minutes=1)
        reminder = await _due(client)
        assert [(a["id"], a["reminder"]) for a in reminder] == [(x["id"], True)], (
            f"the reminder still goes out a day after the send: {reminder}"
        )


async def test_a_reminder_reported_within_the_lease_of_the_first_send_counts(
    app: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reminders after six minutes, leases of fifteen: the reminder's report comes eight minutes after the first
    send's, inside the window a second poll's copy would be. It was handed out as the reminder, so it is one."""
    await _ada_owns(app, quests=1)
    await _put(app, ALWAYS + "  remind_hours: 0.1\n")
    later = [timedelta(0)]
    _shift_feed(monkeypatch, later)
    await _loop_at(app, later).tick()
    async with _client(app) as client:
        (ask,) = await _due(client)
        assert (await client.post(f"/api/v1/asks/{ask['id']}/sent", json={"message_ref": "m"})).status_code == 200
        later[0] = timedelta(minutes=7)
        assert [a["reminder"] for a in await _due(client)] == [True], "the reminder is due after six minutes"
        later[0] = timedelta(minutes=8)
        reported = await client.post(f"/api/v1/asks/{ask['id']}/sent", json={"message_ref": "r"})
        assert (reported.json()["state"], reported.json()["reminders"]) == ("reminded", 1), reported.text
        later[0] = timedelta(minutes=30)
        assert await _due(client) == [], "and no second reminder goes out"


# Two keys of one organization poll at once.

MASTER = "master-key"
#: A key covering both of acme's repositories, and one covering only acme/gadgets.
BOTH = "emb_" + "b" * 43
GADGETS = "emb_" + "g" * 43
#: ada's open quests, worst first, alternating between the two repositories.
QUESTS = [(f"{repo[5]}{k}", repo) for k in range(3) for repo in ("acme/widgets", "acme/gadgets")]

#: Set in a poll's own task: only the feed's reads meet, not another reader of the asks table.
_polling: contextvars.ContextVar[bool] = contextvars.ContextVar("polling", default=False)


class Meeting:
    """Holds each poll that has read the live asks until the other one has too, or a second has passed.

    Without a lock both polls read before either writes, and decide on the same
    asks. A poll that cannot read until the other has written waits here for a
    second, then goes on alone.
    """

    def __init__(self) -> None:
        self.arrived = 0
        self.both = asyncio.Event()

    async def meet(self) -> None:
        self.arrived += 1
        if self.arrived >= 2:
            self.both.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.both.wait(), 1.0)


def _key(key_id: str, repos: tuple[str, ...]) -> ApiKeyRow:
    return ApiKeyRow(
        id=key_id,
        user_id="usr-chat",
        name=key_id,
        prefix="emb_xxxxxxxx",
        scope="propose",
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=repos,
    )


@pytest.fixture
async def keyed(db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """The feed on the real settings store and the real asks table, through :data:`BOTH` and :data:`GADGETS`, with
    ada's six quests as the open decisions and an ask queued about each."""
    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key=MASTER)  # type: ignore[call-arg]
    feed = create_app(config)
    feed.state.db = db_with_migrations
    feed.state.settings = SettingsStore(db_with_migrations)
    feed.state.asks_repo = AsksRepository(db_with_migrations)
    feed.state.organizations_repo = OrganizationsRepository(db_with_migrations)
    await feed.state.organizations_repo.register("acme", "github")
    rows = {
        hash_token(BOTH): _key("key-both00000001", ("acme/widgets", "acme/gadgets")),
        hash_token(GADGETS): _key("key-gadgets00001", ("acme/gadgets",)),
    }
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=rows.get)
    keys.touch_last_used = AsyncMock()
    feed.state.api_keys_repo = keys
    chat = MagicMock(disabled=False, email="chat@example.com")
    feed.state.users_repo = MagicMock(get=AsyncMock(return_value=chat), get_by_email=AsyncMock(return_value=None))
    decisions = [
        {"id": f"offer:{quest}", "repo": repo, "stage": "offered", "owners": ["ada"], "actions": []}
        for quest, repo in QUESTS
    ]

    async def open_now(_request: Any) -> list[dict[str, Any]]:
        return [dict(d) for d in decisions]

    monkeypatch.setattr(asks_api, "open_decisions", open_now)
    headers = {"Authorization": f"Bearer {MASTER}", **HEADERS, "Content-Type": "application/yaml"}
    async with AsyncClient(transport=ASGITransport(app=feed), base_url="http://test", headers=headers) as client:
        put = await client.put("/api/v1/config/settings", content=ALWAYS)
        assert put.status_code == 200, put.text
    now = datetime.now(UTC) - timedelta(minutes=1)
    await feed.state.asks_repo.queue([(d["id"], "ada@example.com", now) for d in decisions])
    yield feed
    bust_api_key_cache()


async def test_two_keys_of_one_organization_polling_at_once_share_ada_s_limit_and_no_ask(
    keyed: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each poll's read of the live asks waits until the other's has happened too, unless the other cannot read
    until this one has written. Through either key ada may be handed three asks; at once, the two keys together
    hand her three, and never one ask twice."""
    meeting = Meeting()
    fetch = asyncpg.connection.Connection.fetch

    async def meeting_fetch(self: Any, query: str, *args: Any, **kwargs: Any) -> Any:
        rows = await fetch(self, query, *args, **kwargs)
        if _polling.get() and query.startswith("SELECT * FROM asks WHERE state = ANY"):
            await meeting.meet()
        return rows

    monkeypatch.setattr(asyncpg.connection.Connection, "fetch", meeting_fetch)

    async def poll(key: str) -> list[str]:
        _polling.set(True)
        headers = {"Authorization": f"Bearer {key}", **HEADERS}
        transport = ASGITransport(app=keyed)
        async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
            resp = await client.get("/api/v1/asks/due")
        assert resp.status_code == 200, resp.text
        return [a["decision"]["id"] for a in resp.json()["asks"]]

    both, gadgets = await asyncio.gather(poll(BOTH), poll(GADGETS))
    assert meeting.arrived == 2, f"each poll read the live asks once: {meeting.arrived}"
    twice = sorted(set(both) & set(gadgets))
    assert not twice, f"two polls at once each handed out {twice}: {both} through one key, {gadgets} through the other"
    handed = len(both) + len(gadgets)
    assert handed == 3, (
        f"ada may hold three asks about acme's decisions: two keys polling at once handed her {handed}: "
        f"{both} and {gadgets}"
    )


#: What happens to ada's worst ask while a poll decides: how it stood before the poll, and what another connection
#: writes just after the poll read the live asks.
MEANWHILE: dict[str, tuple[str | None, str]] = {
    "a first send answered": (None, "UPDATE asks SET state = 'answered' WHERE id = $1"),
    "a reminder reported sent": (
        "UPDATE asks SET state = 'sent', sent_at = now() - interval '2 days' WHERE id = $1",
        "UPDATE asks SET state = 'reminded', reminders = 1 WHERE id = $1",
    ),
}


@pytest.mark.parametrize("meanwhile", MEANWHILE)
async def test_an_ask_changed_while_a_poll_decides_is_not_handed_out(
    keyed: Any, monkeypatch: pytest.MonkeyPatch, meanwhile: str
) -> None:
    """ada's worst ask, a first send or a reminder due, is answered or reported sent on another connection just
    after the poll read the live asks: the poll leases the rest of what it picked and hands out only those."""
    before, during = MEANWHILE[meanwhile]
    asks: AsksRepository = keyed.state.asks_repo
    worst = next(a["id"] for a in await asks.live() if a["decision_id"] == "offer:w0")
    if before:
        await keyed.state.db.execute(before, worst)
    fetch = asyncpg.connection.Connection.fetch

    async def changing_fetch(self: Any, query: str, *args: Any, **kwargs: Any) -> Any:
        rows = await fetch(self, query, *args, **kwargs)
        if _polling.get() and query.startswith("SELECT * FROM asks WHERE state = ANY"):
            _polling.set(False)
            await keyed.state.db.execute(during, worst)
        return rows

    monkeypatch.setattr(asyncpg.connection.Connection, "fetch", changing_fetch)
    _polling.set(True)
    headers = {"Authorization": f"Bearer {BOTH}", **HEADERS}
    async with AsyncClient(transport=ASGITransport(app=keyed), base_url="http://test", headers=headers) as client:
        resp = await client.get("/api/v1/asks/due")
    assert resp.status_code == 200, resp.text
    handed = [(a["decision"]["id"], a["reminder"]) for a in resp.json()["asks"]]
    assert handed == [("offer:g0", False), ("offer:w1", False)], (
        f"{meanwhile}: that ask is left out, and nothing in its place: {handed}"
    )
