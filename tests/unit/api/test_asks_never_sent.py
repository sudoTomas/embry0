"""An ask expires ``expire_days`` after it was sent, never while it waits unsent (#295), on a real database.

The production ask loop, the real asks table and the real feed. One person
owns 2,000 open decisions and may hold three unanswered asks: the first
tick queues an ask about every decision the loop reads, the feed hands out
three, and they are sent. A week later only those three expire. Before #295
an ask expired a week after it was queued, sent or not, and its person was
never asked about that decision again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.v1 import asks as asks_api
from embry0.services.ask_loop import AskLoop
from tests.unit.api.test_asks_per_organization import DEPLOYMENT, _put, app  # noqa: F401
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401

pytestmark = pytest.mark.requires_postgres

OPEN = 2_000


async def test_a_week_on_only_the_asks_that_were_sent_expire(app: Any) -> None:  # noqa: F811
    await _put(app, DEPLOYMENT.replace("09-18", "00-24").replace("mon-fri", "mon-sun"))
    await app.state.repo_preferences_repo.upsert("acme/widgets", owners=["ada"])
    db = app.state.db
    await db.execute(
        "INSERT INTO issues (id, title, repo, created_at) SELECT 'iss-' || lpad(g::text, 12, '0'), 'Issue ' || g, "
        "'acme/widgets', now() - make_interval(secs => $1 - g) FROM generate_series(1, $1) g",
        OPEN,
    )
    production = app.state.ask_loop
    later = [timedelta(0)]
    loop = AskLoop(
        settings=production.settings,
        asks=app.state.asks_repo,
        decisions=production.decisions,
        people=production.people,
        clock=lambda: datetime.now(UTC) + later[0],
    )

    first = await loop.tick()
    queued = first["queued"]
    assert queued >= 500, f"the first tick queues an ask about each decision the loop reads: {first}"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as client:
        due = (await client.get("/api/v1/asks/due")).json()["asks"]
        for ask in due:
            assert (await client.post(f"/api/v1/asks/{ask['id']}/sent", json={"message_ref": "m"})).status_code == 200
    assert len(due) == 3, f"ada may hold three unanswered asks, so the feed hands out three: {len(due)}"

    later[0] = timedelta(days=7, minutes=1)
    week_on = await loop.tick()
    never_sent_expired = await db.fetchval("SELECT count(*) FROM asks WHERE state = 'expired' AND sent_at IS NULL")
    assert never_sent_expired == 0, (
        f"a week on, {never_sent_expired} asks that were never sent expired: they waited behind ada's limit, and "
        f"she would never have been asked about those decisions again: {week_on}"
    )
    assert week_on == {"expired": 3, "withdrawn": 0, "queued": 0}, (
        f"only the three asks sent a week ago expire: {week_on}"
    )
    live = await app.state.asks_repo.live()
    assert len(live) == queued - 3 and all(a["state"] == "queued" for a in live), (
        f"every ask never sent still waits in the queue: {len(live)} of {queued - 3}"
    )
    assert await loop.tick() == {"expired": 0, "withdrawn": 0, "queued": 0}, "and the next tick changes nothing"


def _shift_feed(monkeypatch: pytest.MonkeyPatch, later: list[timedelta]) -> None:
    """Run the feed on the loop's clock, ``later`` ahead of the real one: it reads ``datetime.now``."""

    class Shifted(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:
            return datetime.now(tz) + later[0]

    monkeypatch.setattr(asks_api, "datetime", Shifted)


def _loop_at(app: Any, later: list[timedelta]) -> AskLoop:  # noqa: F811 - the app, not the fixture
    production = app.state.ask_loop
    return AskLoop(
        settings=production.settings,
        asks=app.state.asks_repo,
        decisions=production.decisions,
        people=production.people,
        clock=lambda: datetime.now(UTC) + later[0],
    )


async def _issue(db: Any, issue_id: str, repo: str, seconds_ago: int) -> None:
    await db.execute(
        "INSERT INTO issues (id, title, repo, created_at) VALUES ($1, $1, $2, now() - make_interval(secs => $3))",
        issue_id,
        repo,
        seconds_ago,
    )


async def _due(client: AsyncClient) -> list[dict[str, Any]]:
    return list((await client.get("/api/v1/asks/due")).json()["asks"])


async def test_an_ask_handed_out_but_never_reported_sent_frees_its_slot_then_is_asked_again(
    app: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The assistant cannot deliver one of ada's asks and never reports it sent. The ask never handed out goes
    before it for her third place; it expires expire_days after it was handed out, and she is asked about
    that decision again expire_days after that."""
    await _put(app, DEPLOYMENT.replace("09-18", "00-24").replace("mon-fri", "mon-sun"))
    await app.state.repo_preferences_repo.upsert("acme/widgets", owners=["ada"])
    for k in range(4):
        await _issue(app.state.db, f"iss-{k}", "acme/widgets", 100 - k)
    later = [timedelta(0)]
    loop = _loop_at(app, later)
    _shift_feed(monkeypatch, later)
    assert (await loop.tick())["queued"] == 4
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as client:
        first = await _due(client)
        stuck = first[0]["decision"]["id"]
        for ask in first[1:]:
            await client.post(f"/api/v1/asks/{ask['id']}/sent", json={"message_ref": "m"})
        second = await _due(client)
        assert len(second) == 1 and second[0]["decision"]["id"] != stuck, (
            f"ada holds two sent asks; her third place goes to the ask never handed out, not the undelivered one: "
            f"{[a['decision']['id'] for a in second]}"
        )
        await client.post(f"/api/v1/asks/{second[0]['id']}/sent", json={"message_ref": "m"})
        assert await _due(client) == [], "she holds three: the undelivered one waits"

        later[0] = timedelta(days=7, minutes=1)
        week_on = await loop.tick()
        assert week_on["expired"] == 4, f"the three sent asks and the undelivered one expire a week on: {week_on}"
        assert await _due(client) == [], "the undelivered ask is not handed out again"
        assert (await loop.tick())["queued"] == 0, "nor asked again about the undelivered decision yet"

        later[0] = timedelta(days=14, minutes=2)
        assert (await loop.tick())["queued"] == 1, "expire_days after it expired, ada is asked about it again"
        live = {a["decision_id"] for a in await app.state.asks_repo.live() if a["state"] == "queued"}
        assert f"{stuck}" in live, f"the new ask is about the undelivered decision: {live}"


async def _skipped_owners(app: Any, quests_each: int) -> None:  # noqa: F811 - the app, not the fixture
    """Twenty owners with no embry0 person (github:<login>), whom the assistant skips, each owning
    ``quests_each`` open quests in acme/widgets, and ada owning one older quest in acme/gadgets."""
    await _put(app, DEPLOYMENT.replace("09-18", "00-24").replace("mon-fri", "mon-sun"))
    await app.state.repo_preferences_repo.upsert("acme/widgets", owners=[f"u{i:02d}" for i in range(20)])
    await app.state.repo_preferences_repo.upsert("acme/gadgets", owners=["ada"])
    for k in range(quests_each):
        await _issue(app.state.db, f"iss-w{k}", "acme/widgets", 10 + k)
    await _issue(app.state.db, "iss-ada", "acme/gadgets", 1000)


def _ada_in(due: list[dict[str, Any]]) -> bool:
    return any(a["recipient"]["github_login"] == "ada" for a in due)


async def test_ada_reaches_the_page_while_owners_the_assistant_skips_hold_many_asks(
    app: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Twenty owners the assistant skips hold 80 asks, which fill the first page of 50 ahead of ada, whose
    quest ranks last. The asks they were handed are not reported sent, so the next page puts the asks never
    handed out first, ada's among them."""
    await _skipped_owners(app, quests_each=4)
    later = [timedelta(0)]
    loop = _loop_at(app, later)
    _shift_feed(monkeypatch, later)
    assert (await loop.tick())["queued"] == 81
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as client:
        page = await _due(client)
        assert len(page) == 50 and not _ada_in(page), "the skipped owners' asks fill the first page"
        later[0] = timedelta(minutes=10)
        page = await _due(client)
        assert _ada_in(page), (
            f"the next poll hands out the asks never handed out first, ada's among them: "
            f"{[a['recipient']['github_login'] for a in page]}"
        )

        later[0] = timedelta(days=7, minutes=1)
        assert (await loop.tick())["expired"] >= 50, "the asks handed out and never reported sent expire a week on"


async def test_ada_reaches_the_page_within_a_day_when_the_skipped_owners_hold_ten_quests_each(
    app: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reviewer's case: each skipped owner holds ten quests, so their waves of three overlap their
    week-long waits and, worst first, ada never reached the page in ten weeks. Asks never handed out
    come first now: polled every six hours, ada is on a page within the first day."""
    await _skipped_owners(app, quests_each=10)
    later = [timedelta(0)]
    loop = _loop_at(app, later)
    _shift_feed(monkeypatch, later)
    seen: list[bool] = []
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as client:
        for hours in range(0, 24 + 1, 6):
            later[0] = timedelta(hours=hours, minutes=1)
            await loop.tick()
            seen.append(_ada_in(await _due(client)))
    assert any(seen), f"ada reaches a page within the first day of polls every six hours: {seen}"


async def test_a_peek_shows_the_page_and_hands_nothing_out(
    app: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plain call is the assistant's hand-out and starts each first send's expiry; ?peek=1 does not."""
    await _put(app, DEPLOYMENT.replace("09-18", "00-24").replace("mon-fri", "mon-sun"))
    await app.state.repo_preferences_repo.upsert("acme/widgets", owners=["ada"])
    for k in range(5):
        await _issue(app.state.db, f"iss-{k}", "acme/widgets", 100 - k)
    later = [timedelta(0)]
    loop = _loop_at(app, later)
    _shift_feed(monkeypatch, later)
    await loop.tick()

    async def stamped() -> int:
        return int(await app.state.db.fetchval("SELECT count(*) FROM asks WHERE handed_out_at IS NOT NULL"))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as client:
        peeked = (await client.get("/api/v1/asks/due", params={"peek": "1"})).json()["asks"]
        assert len(peeked) == 3 and await stamped() == 0, "a peek returns ada's page of three and stamps none"
        handed = (await client.get("/api/v1/asks/due")).json()["asks"]
        assert [a["id"] for a in handed] == [a["id"] for a in peeked], "the same page"
        assert await stamped() == 3, "the plain call hands it out: its three first sends are stamped"
