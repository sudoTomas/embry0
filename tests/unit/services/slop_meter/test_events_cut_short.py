"""A forge read cut short is not scored as if it were complete (#465).

The event reader set ``EventBatch.more`` where a read stopped short (a
listing's page cap, the reviews or the logins one pass may read, a listing
answered with an error) and nothing read it, so a repository whose read
stopped there was scored as if every event had been read. Now a read a cap
cut short is scored once caught up, as a history is; a read the forge stopped
with an answer a retry can change keeps the previous score, with nothing from
that call on read or passed; and a call the forge refuses otherwise is logged
and passed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.services.background_scope import Registered
from embry0.services.slop_meter.events import ApiBudget, EventReader
from embry0.services.slop_meter.loop import SlopMeterLimits, SlopMeterLoop
from tests.fixtures.areas import no_areas_of
from tests.unit.services.slop_meter.test_loop import (
    FORGE,
    REPO,
    START,
    FakeCalls,
    FakeForge,
    FakeMirror,
    FakeResponse,
    FakeStore,
    NoExits,
    _history,
    _read,
    _read_all,
    no_audit,
)

SINCE = datetime(2025, 1, 15, tzinfo=UTC)


class Answering(FakeForge):
    """FakeForge, with the paths in ``answers`` answered with that status instead."""

    def __init__(self, answers: dict[str, int] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.answers = dict(answers or {})

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        for suffix, status in self.answers.items():
            if path.endswith(suffix):
                self.calls.append((path, dict(params or {})))
                return FakeResponse(status, {"message": "Server Error"})
        return await super().get(path, repo=repo, params=params)


def _reader(forge: FakeForge, **kwargs: Any) -> EventReader:
    return EventReader(
        forge,
        REPO,
        comments_since=SINCE,
        review_comments_since=SINCE,
        reviews_since=SINCE,
        max_pages=kwargs.pop("max_pages", 2),
        max_pulls=kwargs.pop("max_pulls", 10),
        max_logins=kwargs.pop("max_logins", 5),
        **kwargs,
    )


def _stopped(batch: Any) -> str:
    return str(getattr(batch, "stopped", ""))


# ---- the reader ----


async def _read_until_stopped(reader: EventReader) -> None:
    """Read until the forge stops the read; a read that is never stopped fails."""
    try:
        from embry0.services.slop_meter.events import ForgeStopped
    except ImportError:
        pytest.fail("the reader has no way to stop where the forge stopped it")
    with pytest.raises(ForgeStopped):
        await _read_all(reader, ApiBudget(100, 0))
    assert not reader.done and reader.place is not None, "a read the forge stopped must keep its place"


@pytest.mark.asyncio
async def test_a_listing_the_forge_answers_503_stops_the_read_where_it_is() -> None:
    forge = Answering({"/pulls/comments": 503})
    reader = _reader(forge)
    await _read_until_stopped(reader)
    batch = reader.batch
    assert _stopped(batch) == f"/repos/{REPO}/pulls/comments answered 503", f"the stop is not recorded: {batch}"
    assert [path for path, _ in forge.calls] == [f"/repos/{REPO}/issues/comments", f"/repos/{REPO}/pulls/comments"], (
        f"the read went on past the call the forge stopped: {forge.calls}"
    )
    assert batch.review_comments_since == SINCE and batch.reviews_since == SINCE and batch.logins == {}
    assert (reader.place.stage, reader.place.pages) == (1, 0), f"the place is not at the stopped call: {reader.place}"
    assert batch.more is False, "a stop is not a cap"


@pytest.mark.asyncio
async def test_reviews_the_forge_answers_503_keep_their_pull_request_and_the_cursor_short_of_it() -> None:
    forge = Answering({"/reviews": 503})
    reader = _reader(forge)
    await _read_until_stopped(reader)
    assert _stopped(reader.batch).endswith("/pulls/7/reviews answered 503"), f"the stop is not recorded: {reader.batch}"
    assert reader.batch.reviews_since == SINCE, (
        f"the reviews cursor moved past a pull request whose reviews were not read: {reader.batch.reviews_since}"
    )
    assert [n for _at, n in reader.place.pulls] == [7], f"the pull request left its line: {reader.place}"


@pytest.mark.asyncio
async def test_a_login_the_forge_answers_503_is_left_to_look_up_again() -> None:
    forge = Answering({"/commits": 502})
    reader = _reader(forge)
    await _read_until_stopped(reader)
    assert _stopped(reader.batch) == f"/repos/{REPO}/commits answered 502", f"the stop is not recorded: {reader.batch}"
    assert reader.batch.logins == {}, f"a login not looked up was kept with no email: {reader.batch.logins}"
    assert reader.place.logins and reader.place.logins[0] == "ana", f"the login left its list: {reader.place}"


@pytest.mark.asyncio
async def test_a_call_the_forge_refuses_is_logged_and_passed() -> None:
    forge = Answering({"/pulls/comments": 403})
    reader = _reader(forge)
    with capture_logs() as logs:
        await _read_all(reader, ApiBudget(100, 0))
    batch = reader.batch
    assert _stopped(batch) == "" and batch.more is False, f"a refusal no retry changes holds nothing: {batch}"
    assert batch.reviews_since == datetime(2025, 2, 5, tzinfo=UTC) and "bo" in batch.logins, "the read went on"
    refused = [e for e in logs if e["event"] == "slop_meter_events_refused"]
    assert [(e["path"], e["status"]) for e in refused] == [(f"/repos/{REPO}/pulls/comments", 403)], logs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limits", "why"),
    [({"max_pages": 1}, "page cap"), ({"max_pulls": 0}, "reviews cap"), ({"max_logins": 1}, "logins cap")],
)
async def test_a_cap_marks_the_read_as_cut_short(limits: dict[str, int], why: str) -> None:
    reader = _reader(FakeForge(comment_pages=3), **limits)
    await _read_all(reader, ApiBudget(100, 0))
    assert reader.batch.more is True and _stopped(reader.batch) == "", f"the {why} did not mark the read"


# ---- the loop ----


class Since:
    """A GitHub with ``comments`` issue comments, one a day, one a page, that honours ``since``."""

    def __init__(self, comments: int) -> None:
        self.comments = comments
        self.fail: int | None = None
        self.calls: list[str] = []

    def _stamp(self, day: int) -> str:
        return f"2025-02-{day:02d}T10:00:00Z"

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        self.calls.append(path)
        if self.fail is not None and path.endswith("/pulls/comments"):
            return FakeResponse(self.fail, {})
        if path.endswith("/issues/comments"):
            # Every page of a listing is asked for with the cursor its first page had, by its number.
            since = (params or {}).get("since")
            days = [d for d in range(1, self.comments + 1) if since is None or d > int(since[8:10])]
            page = int((params or {}).get("page", 1))
            if page > len(days):
                return FakeResponse(200, [])
            day = days[page - 1]
            item = {"id": day, "user": {"login": "ana"}, "created_at": self._stamp(day), "updated_at": self._stamp(day)}
            nxt = f"{FORGE}{path}?page={page + 1}" if page < len(days) else None
            return FakeResponse(200, [item], next_url=nxt)
        if path.endswith("/commits"):
            return FakeResponse(200, [])
        return FakeResponse(200, [])


def _meter_loop(forge: Any, store: FakeStore, **limits: Any) -> SlopMeterLoop:
    records, order = _history(2)

    async def repos() -> Registered:
        return Registered(active=(REPO,))

    async def token_for(repo: str) -> tuple[str, str, str]:
        return ("github", "t", "")

    async def client_for(repo: str) -> Any:
        return forge

    async def bots(repo: str) -> tuple[str, ...]:
        return ()

    return SlopMeterLoop(
        repos=repos,
        exits=NoExits(),
        audit=no_audit,
        read=_read(api_calls_per_tick=100, rate_reserve=0),
        store=store,
        mirror=FakeMirror(records, order),
        token_for=token_for,
        client_for=client_for,
        bots=bots,
        calls=FakeCalls(),
        limits=SlopMeterLimits(commits_per_tick=10, **limits),
        areas_of=no_areas_of,
        clock=lambda: START + timedelta(days=60),
    )


class Counting(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.scored = 0

    async def store(self, repo: str, results: dict[str, Any], computed_at: datetime, config_version: str = "") -> None:
        self.scored += 1
        await super().store(repo, results, computed_at, config_version)


@pytest.mark.asyncio
async def test_a_read_a_cap_cut_short_is_scored_once_caught_up() -> None:
    store = Counting()
    loop = _meter_loop(Since(comments=3), store, event_pages=1)
    assert await loop.tick() is True, "a read a page cap cut short is a backlog, as a history is"
    assert store.scored == 0, "a read a page cap cut short was scored as if it were complete"
    assert await loop.tick() is True and store.scored == 0
    store.versions_of[REPO] = "an earlier version"  # as a score before a settings change recorded it
    assert await loop.rescore_changed() == [] and store.scored == 0, "a rescore would score the short read"
    assert await loop.tick() is False, "the third pass reads the last comment"
    assert store.scored == 1 and {e["login"] for e in await store.events(REPO)} == {"ana"}
    assert len(await store.events(REPO)) == 3, "every comment was read before the score"


@pytest.mark.asyncio
async def test_a_read_the_forge_stopped_keeps_its_previous_score_until_a_run_reads_on() -> None:
    forge, store = Since(comments=1), Counting()
    loop = _meter_loop(forge, store)
    assert await loop.tick() is False and store.scored == 1
    forge.fail = 503
    with capture_logs() as logs:
        backlog = await loop.tick()
    assert store.scored == 1, "a read the forge stopped was scored as if it were complete"
    assert backlog is False, "a forge that answers 503 does not bring the meter back sooner"
    held = [e for e in logs if e["event"] == "slop_meter_score_held"]
    assert held and "pulls/comments answered 503" in held[0]["reason"], logs
    assert await loop.rescore_changed(await _read(api_calls_per_tick=100, rate_reserve=0, event_hours=2)()) == [], (
        "a rescore would score the read the forge stopped"
    )
    forge.fail = None
    assert await loop.tick() is False and store.scored == 2, "a run that reads on scores it again"


@pytest.mark.asyncio
async def test_a_read_with_a_call_the_forge_refuses_is_scored_from_the_rest() -> None:
    forge, store = Since(comments=1), Counting()
    forge.fail = 404
    assert await _meter_loop(forge, store).tick() is False
    assert store.scored == 1, "a refusal no retry changes must not keep a repository from its score"


@pytest.mark.asyncio
async def test_a_call_the_forge_always_stops_is_passed_after_max_failures_and_the_read_is_scored() -> None:
    """A stop counts as a failed call: the read is held at it, but the same call stopped in that many runs in a row
    is passed, so an endpoint that always answers 5xx cannot keep the score frozen for good (the review's probe:
    newer activity arrives, then one listing answers 502 on every run)."""
    from embry0.services.slop_meter.events import MAX_FAILURES

    forge, store = Since(comments=3), Counting()
    loop = _meter_loop(forge, store)
    assert await loop.tick() is False and store.scored == 1
    forge.comments, forge.fail = 6, 502
    scored, held = [], 0
    for _ in range(2 * MAX_FAILURES):
        with capture_logs() as logs:
            await loop.tick()
        held += sum(1 for e in logs if e["event"] == "slop_meter_score_held")
        scored.append(store.scored)
    every_third = [1 + (run + 1) // MAX_FAILURES for run in range(2 * MAX_FAILURES)]
    assert (scored, held) == (every_third, 2 * (MAX_FAILURES - 1)), (
        f"the score after each run with one call stopped every time: {scored}, held {held} times"
    )
    assert {e["login"] for e in await store.events(REPO)} == {"ana"} and len(await store.events(REPO)) == 6, (
        "the newer comments were read"
    )


@pytest.mark.asyncio
async def test_a_stop_stores_the_readers_place_and_the_next_run_goes_on_from_it() -> None:
    forge, store = Since(comments=1), Counting()
    loop = _meter_loop(forge, store)
    forge.fail = 503
    backlog = await loop.tick()
    assert (backlog, store.scored) == (False, 0), f"a read the forge stopped was scored: {backlog}, {store.scored}"
    place = store.states[REPO].get("reader_place")
    assert place is not None and place.get("stage") == 1 and place.get("failures") == 1, (
        f"a read the forge stopped did not keep its place at the stopped call: {place}"
    )
    forge.fail, forge.calls = None, []
    assert await loop.tick() is False and store.scored == 1
    assert forge.calls[0].endswith("/pulls/comments"), (
        f"the next run did not go on from the stopped call: {forge.calls}"
    )
    assert store.states[REPO].get("reader_place") is None, "a finished read keeps no place"
