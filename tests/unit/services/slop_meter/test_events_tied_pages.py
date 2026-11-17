"""Comments that share one update are read on across runs, page by page (#526), and none is left out (#557).

A comment listing goes on from its cursor, the newest update it read. Among
comments that all have the update a page was asked from, the cursor cannot
move, so the listing goes on by page (#510). How far it read was not kept
between runs: a run that stopped inside such a block asked for its first pages
again the next run, from the same cursor, and a credential's share smaller
than the block's pages never read past them. The place a run stops at now
keeps it (``tied_offset``, with ``tied_anchors``, the last comments read), and
the next run goes on from there. Each such page starts on comments read
already, so a comment that an edit or a deletion moved up onto them is still
read (#557).

Pages hold five comments here (``PER_PAGE`` and the fake's page size), so a
block of 23 runs over five of them.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from structlog.testing import capture_logs

import embry0.services.slop_meter.events as events
from embry0.services.slop_meter.events import ApiBudget, BudgetSpent, EventReader, ReaderPlace
from tests.unit.services.slop_meter import test_loop
from tests.unit.services.slop_meter.test_loop import FORGE, REPO, START, FakeResponse

#: The caps the meter reads with here: ten pages a listing, no reviews or logins.
CAPS = {"max_pages": 10, "max_pulls": 0, "max_logins": 0}
#: A page's size here, the reader's and the forge's.
PAGE = 5
#: Comments updated at the same second, five a page: five pages of them.
TIED = 23


@pytest.fixture(autouse=True)
def _pages_of_five(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "PER_PAGE", PAGE)


def Comments(updated: dict[int, int], edit: dict[int, int] | None = None) -> test_loop.Comments:  # noqa: N802
    """The comment listing of test_loop, five a page."""
    return test_loop.Comments(updated, edit, page_size=PAGE)


def _reader(forge: Any, cursor: Any, place: Any) -> EventReader:
    return EventReader(
        forge,
        REPO,
        comments_since=cursor,
        review_comments_since=None,
        reviews_since=None,
        place=ReaderPlace.from_json(place, **CAPS),
        **CAPS,
    )


async def _run(reader: EventReader, calls: int) -> None:
    budget = ApiBudget(calls, 0)
    try:
        while await reader.step(budget):
            pass
    except BudgetSpent:
        pass


async def _runs(forge: Any, *, calls: int, runs: int) -> tuple[set[int], int | None]:
    """The comments read over ``runs`` runs of ``calls`` calls each, and the run that had read them all.

    Each run starts from what the loop stores between runs: the listing's
    cursor and the reader's place, as JSON.
    """
    read: set[int] = set()
    cursor, place, every = START, None, None
    for run in range(1, runs + 1):
        reader = _reader(forge, cursor, place)
        await _run(reader, calls)
        read |= {int(e[0].split(":")[1]) for e in reader.batch.events}
        cursor = reader.batch.comments_since or cursor
        place = None if reader.place is None else reader.place.to_json()
        if every is None and len(read) == TIED:
            every = run
    return read, every


@pytest.mark.parametrize("calls", [1, 2, 3, 100])
async def test_a_block_of_comments_with_one_update_is_read_whatever_the_share(calls: int) -> None:
    """#526's case: comments at one second, 1, 2, 3 or 100 calls a run, over 20 runs."""
    read, every = await _runs(Comments({i: 1 for i in range(1, TIED + 1)}), calls=calls, runs=20)
    assert read == set(range(1, TIED + 1)), f"{calls} call(s) a run: read {len(read)} of {TIED} ({sorted(read)})"
    assert every is not None, f"{calls} call(s) a run did not read them in 20 runs"


async def test_the_place_keeps_how_far_a_listing_read_among_tied_comments() -> None:
    # Two older comments, before the cursor: a page asked for without it would start at them.
    forge = Comments({101: -1, 102: -1, **{i: 1 for i in range(1, TIED + 1)}})
    reader = _reader(forge, START + timedelta(days=1), None)
    await _run(reader, 2)
    place = reader.place
    assert place is not None and place.stage == 0
    read = sorted(int(e[0].split(":")[1]) for e in reader.batch.events)
    assert (place.tied_offset, place.tied_anchors) == (len(read), tuple(read[-3:])), (
        f"how far the listing read among the tied comments, and its last ones, are not kept: {place} after {read}"
    )
    assert place.next_page is None, "a comment listing's place is its own field, never the pull request listing's"
    assert ReaderPlace.from_json(place.to_json(), **CAPS) == place, "the place does not survive being stored"

    resumed = _reader(forge, reader.batch.comments_since, place.to_json())
    await _run(resumed, 1)
    more = sorted(int(e[0].split(":")[1]) for e in resumed.batch.events)
    assert more and more[0] == read[-1] + 1, f"the next run does not go on after {read[-1]}: {more}"


async def test_the_place_is_dropped_once_the_tied_comments_are_passed() -> None:
    """Eight comments share one update, then two later ones: past the block the listing goes on from its cursor."""
    forge = Comments({**{i: 1 for i in range(1, 9)}, 9: 2, 10: 3, 11: 4, 12: 5, 13: 6, 14: 7})
    reader = _reader(forge, START + timedelta(days=1), None)
    budget = ApiBudget(100, 0)
    for _step in range(20):  # the block's pages, until one reaches 9, a later update
        if reader.batch.comments_since != START + timedelta(days=1) or not await reader.step(budget):
            break
    place = reader.place
    assert place is not None and place.stage == 0 and place.tied_offset is None, (
        f"a listing past the tied comments still goes on among them: {place}"
    )
    assert reader.batch.comments_since > START + timedelta(days=1), "the cursor moved past the block"


async def test_a_place_stored_before_the_offset_was_kept_goes_on_from_the_cursor() -> None:
    """A place with no ``tied_offset`` (stored before it was kept, or with #526's ``tied_page``), even one naming a
    comment listing's ``next_page`` as earlier readers wrote it, still loads, and the listing goes on from its
    cursor at its first page."""
    forge = Comments({i: 1 for i in range(1, TIED + 1)})
    calls: list[dict[str, Any]] = []
    original = forge.get

    async def get(path: str, *, repo: str, params: dict[str, Any] | None = None) -> Any:
        calls.append(dict(params or {}))
        return await original(path, repo=repo, params=params)

    forge.get = get  # type: ignore[method-assign]
    for stored in (
        {"stage": 0, "pages": 2},
        {"stage": 0, "pages": 2, "tied_page": 3},
        {"stage": 0, "pages": 2, "next_page": 3, "pulls": [], "found": 0, "seen": [], "pulls_left": 0},
    ):
        place = ReaderPlace.from_json(stored, **CAPS)
        assert place is not None and place.tied_offset is None, stored
        calls.clear()
        reader = _reader(forge, START + timedelta(days=1), stored)
        await _run(reader, 1)
        assert "page" not in calls[0], f"an earlier place is read as a page among tied comments: {calls[0]}"


@pytest.mark.parametrize(
    "stored",
    [
        {"stage": 2, "pages": 2, "tied_offset": 3},
        {"stage": 0, "pages": 2, "tied_offset": 0},
        {"stage": 0, "pages": 2, "tied_offset": 10_000 * PAGE + 1},
        {"stage": 0, "pages": 2, "tied_offset": True},
        {"stage": 0, "pages": 2, "tied_offset": "3"},
        {"stage": 0, "pages": 2, "tied_offset": 3, "tied_anchors": [1, 2, 3, 4]},
        {"stage": 0, "pages": 2, "tied_offset": 3, "tied_anchors": [True]},
        {"stage": 0, "pages": 2, "tied_offset": 3, "tied_anchors": "1"},
        {"stage": 0, "pages": 2, "tied_anchors": [1]},
        {"stage": 0, "pages": 2, "tied_offset": 3, "tied_back": -5},
        {"stage": 0, "pages": 2, "tied_offset": 3, "tied_back": True},
        {"stage": 0, "pages": 2, "tied_offset": 3, "tied_back": "5"},
        {"stage": 0, "pages": 2, "tied_offset": 3, "tied_back": 10_000 * PAGE},
        {"stage": 0, "pages": 2, "tied_back": 5},
    ],
    ids=[
        "pull-listing",
        "none-read",
        "past-the-bound",
        "bool",
        "text",
        "too-many-anchors",
        "bool-anchor",
        "text-anchors",
        "anchors-without-offset",
        "negative-back",
        "bool-back",
        "text-back",
        "back-past-the-bound",
        "back-without-offset",
    ],
)
def test_a_page_no_reader_could_have_stored_is_no_place(stored: dict[str, Any]) -> None:
    assert ReaderPlace.from_json(stored, **CAPS) is None, stored


def test_the_pull_request_listings_place_keeps_its_next_page_and_no_tied_one() -> None:
    """The pull request listing goes on from ``next_page``; a tied page there would make its place no place."""
    stored = ReaderPlace(stage=2, pages=1, next_page=2)
    reader = _reader(Comments({}), START, stored.to_json())
    place = reader.place
    assert place is not None and (place.next_page, place.tied_offset) == (2, None), place
    assert ReaderPlace.from_json(place.to_json(), **CAPS) == place, "the pull request listing's place does not load"


def test_a_place_among_tied_comments_past_the_page_cap_is_a_place() -> None:
    """Pages among comments that share one update are not counted against the cap (#538), so a place may be past
    it, and past the pages it counted."""
    for stored in (
        {"stage": 0, "pages": 1, "tied_offset": 12, "tied_anchors": [10, 11, 12]},
        {"stage": 1, "pages": 10, "tied_offset": 55, "tied_anchors": ["53", "54", "55"]},
    ):
        place = ReaderPlace.from_json(stored, **CAPS)
        assert place is not None and place.tied_offset == stored["tied_offset"], (
            f"a place among tied comments past the pages it counted is refused, so the read starts over: {stored}"
        )


@pytest.mark.parametrize("pages", [1, 2])
@pytest.mark.parametrize("calls", [1, 3, 100])
async def test_a_block_of_comments_with_one_update_longer_than_the_page_cap_is_read_through(
    calls: int, pages: int
) -> None:
    """#538: comments at one second, a page cap of 2. The cap cut the block at its second page every pass, and the
    next pass asked for its first pages again, for good. A cap of 1 counts the first page from the cursor, which
    moves it to that second; the next pass reads the block through."""
    read: set[int] = set()
    cursor, place, more = START, None, True
    caps = {**CAPS, "max_pages": pages}
    forge = Comments({i: 1 for i in range(1, TIED + 1)})
    for _run_number in range(20):
        reader = EventReader(
            forge,
            REPO,
            comments_since=cursor,
            review_comments_since=None,
            reviews_since=None,
            place=ReaderPlace.from_json(place, **caps),
            # Their authors are known, so no login lookup (a cap of 0 here) marks the read as cut short.
            known_logins=[f"user{i}" for i in range(1, TIED + 1)],
            **caps,
        )
        await _run(reader, calls)
        read |= {int(e[0].split(":")[1]) for e in reader.batch.events}
        cursor = reader.batch.comments_since or cursor
        place = None if reader.place is None else reader.place.to_json()
        more = reader.batch.more
    assert read == set(range(1, TIED + 1)), f"page cap {pages}, {calls} call(s) a run: read {len(read)} of {TIED}"
    assert more is False, "the listing still says it was cut at the page cap, so the repository is never scored"


async def test_the_page_cap_still_counts_the_pages_that_move_the_cursor() -> None:
    """Fourteen comments, each its own update: the cap of 2 cuts the listing after two pages, as before (the second
    asked from the cursor the first moved, so comment 5 again)."""
    reader = EventReader(
        Comments({i: i for i in range(1, 15)}),
        REPO,
        comments_since=START,
        review_comments_since=None,
        reviews_since=None,
        **{**CAPS, "max_pages": 2},
    )
    await _run(reader, 100)
    read = {int(e[0].split(":")[1]) for e in reader.batch.events}
    assert (reader.batch.more, read) == (True, set(range(1, 10))), (
        f"the cap no longer cuts a listing that moves: {read}"
    )


async def test_the_repository_of_such_a_block_is_scored_once_it_is_read() -> None:
    """The loop: a read the page cap cut short waits for the next pass (#465); this one ends, and is scored."""
    from tests.unit.services.slop_meter.test_events_cut_short import Counting, _meter_loop

    store = Counting()
    loop = _meter_loop(Comments({i: 1 for i in range(1, TIED + 1)}), store, event_pages=2)
    backlogs = [await loop.tick() for _ in range(4)]
    assert store.scored >= 1 and backlogs[-1] is False, (
        f"a block of comments with one update longer than the page cap: backlog {backlogs}, scored {store.scored}"
    )


async def test_a_block_many_times_the_page_cap_is_read_one_call_a_run() -> None:
    """Forty comments at one second, eight pages, a cap of 2 and one call a run: the place each run stores must
    still load (its pages counted stay within the cap), or every run would start the block over."""
    tied = 40
    read: set[int] = set()
    cursor, place = START, None
    caps = {**CAPS, "max_pages": 2}
    forge = Comments({i: 1 for i in range(1, tied + 1)})
    for _run_number in range(60):
        reader = EventReader(
            forge,
            REPO,
            comments_since=cursor,
            review_comments_since=None,
            reviews_since=None,
            place=ReaderPlace.from_json(place, **caps),
            known_logins=[f"user{i}" for i in range(1, tied + 1)],
            **caps,
        )
        await _run(reader, 1)
        read |= {int(e[0].split(":")[1]) for e in reader.batch.events}
        cursor = reader.batch.comments_since or cursor
        place = None if reader.place is None else reader.place.to_json()
    assert read == set(range(1, tied + 1)), f"one call a run, 60 runs: read {len(read)} of {tied} ({sorted(read)})"


class Stuck:
    """A forge that answers every comment listing page with the same page and a link to page 2, never ending: the
    same two comments at one second, an empty page, or two comments with no update."""

    def __init__(self, kind: str) -> None:
        self.kind, self.calls = kind, 0

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        self.calls += 1
        if not path.endswith("/issues/comments"):
            return FakeResponse(200, [])
        at = (START + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        items: list[dict[str, Any]] = [
            {"id": i, "user": {"login": f"user{i}"}, "created_at": at, "updated_at": at} for i in (1, 2)
        ]
        if self.kind == "empty":
            items = []
        elif self.kind == "no update":
            items = [{k: v for k, v in item.items() if k != "updated_at"} for item in items]
        return FakeResponse(200, items, next_url=f"{FORGE}{path}?page=2")


@pytest.mark.parametrize("kind", ["same page", "empty", "no update"])
async def test_pages_that_never_move_the_cursor_are_cut_short_rather_than_spending_the_run(kind: str) -> None:
    """Pages that do not move the cursor are not counted against the cap (#538), so a forge answering such a page
    for good would take every call of every run, never end the listing nor reach the rest of the read, and report
    nothing cut short. A page asked for by its number that brings no comment the read has not seen ends it."""
    forge = Stuck(kind)
    cursor = START + timedelta(days=1) if kind == "same page" else START
    place: Any = None
    caps = {**CAPS, "max_pages": 2}
    runs = []
    with capture_logs() as logs:
        for _run_number in range(4):
            before = forge.calls
            reader = EventReader(
                forge,
                REPO,
                comments_since=cursor,
                review_comments_since=None,
                reviews_since=None,
                place=ReaderPlace.from_json(place, **caps),
                known_logins=["user1", "user2"],
                **caps,
            )
            await _run(reader, 50)
            runs.append((forge.calls - before, reader.done, reader.batch.more))
            cursor = reader.batch.comments_since or cursor
            place = None if reader.place is None else reader.place.to_json()
    assert all(calls <= 4 and done and more for calls, done, more in runs), (
        f"{kind}: each run (calls, read done, cut short) ends the listing in a few calls and says so: {runs}"
    )
    stopped = [e for e in logs if e["event"] == "slop_meter_tied_pages_stopped"]
    assert len(stopped) == 4 and all(e["repo"] == REPO for e in stopped), f"{kind}: one warning a run: {stopped}"


async def test_a_read_stops_at_the_furthest_tied_page_rather_than_storing_one_it_cannot_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAX_TIED_PAGE bounded only a stored place, so a run could read past it, store a place that then failed to
    load, and start the block again at its first page, with nothing said. Here the bound is three pages (15
    comments) of a block of five: every run stops there, cut short and logged, and every place it stores loads."""
    monkeypatch.setattr(events, "MAX_TIED_PAGE", 3)
    forge = Comments({i: 1 for i in range(1, TIED + 1)})
    cursor, place, pages = START, None, []
    with capture_logs() as logs:
        for _run_number in range(12):
            reader = EventReader(
                forge,
                REPO,
                comments_since=cursor,
                review_comments_since=None,
                reviews_since=None,
                place=ReaderPlace.from_json(place, **CAPS),
                known_logins=[f"user{i}" for i in range(1, TIED + 1)],
                **CAPS,
            )
            await _run(reader, 1)
            cursor = reader.batch.comments_since or cursor
            place = None if reader.place is None else reader.place.to_json()
            pages.append(None if place is None else place.get("tied_offset"))
            assert place is None or ReaderPlace.from_json(place, **CAPS) is not None, f"a stored place loads: {place}"
    assert max(p or 0 for p in pages) <= 3 * PAGE, f"no run reads past the bound: {pages}"
    stopped = [e for e in logs if e["event"] == "slop_meter_tied_pages_stopped"]
    assert stopped and all(e["repo"] == REPO and e["reason"] == "past the page bound" for e in stopped), (
        f"the read stops at the bound, saying so: {stopped}"
    )


class BothListings:
    """Issue comments and review comments, five a page (fewer when asked for), all at one second, with the same ids
    in each listing (the forge numbers them apart; nothing says it must)."""

    def __init__(self, count: int) -> None:
        self.count = count

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        if not path.endswith(("/issues/comments", "/pulls/comments")):
            return FakeResponse(200, [])
        page = int((params or {}).get("page", 1))
        size = min(int((params or {}).get("per_page", PAGE)), PAGE)
        at = (START + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ids = list(range(1, self.count + 1))[(page - 1) * size : page * size]
        items = [{"id": i, "user": {"login": f"user{i}"}, "created_at": at, "updated_at": at} for i in ids]
        nxt = f"{FORGE}{path}?page={page + 1}" if page * size < self.count else None
        return FakeResponse(200, items, next_url=nxt)


async def test_each_listing_counts_only_its_own_comments_as_read() -> None:
    """The comments a read has seen, which end a run of pages that bring nothing new, are each listing's own: a
    review comment with an issue comment's id is not one already read."""
    reader = EventReader(
        BothListings(14),
        REPO,
        comments_since=START,
        review_comments_since=START,
        reviews_since=None,
        known_logins=[f"user{i}" for i in range(1, 15)],
        **CAPS,
    )
    await _run(reader, 100)
    read = sorted(e[0] for e in reader.batch.events)
    assert set(read) == {f"{p}:{i}" for p in ("ic", "rc") for i in range(1, 15)}, f"both listings read whole: {read}"
    assert reader.batch.more is False, "neither listing was cut short"


# ---- a comment that leaves the block between two runs (#557) ----------------------------------------------------


async def _runs_changing(
    forge: Any, *, calls: int, runs: int, change: Any, after_run: int
) -> tuple[set[int], list[dict[str, Any] | None]]:
    """The comments read over ``runs`` runs, ``change(forge)`` applied once, after run ``after_run``; and each place
    stored."""
    read: set[int] = set()
    cursor, place, places = START + timedelta(days=1), None, []
    for run in range(1, runs + 1):
        reader = _reader(forge, cursor, place)
        await _run(reader, calls)
        read |= {int(e[0].split(":")[1]) for e in reader.batch.events}
        cursor = reader.batch.comments_since or cursor
        place = None if reader.place is None else reader.place.to_json()
        places.append(place)
        if run == after_run:
            change(forge)
    return read, places


def _edit(*ids: int) -> Any:
    def change(forge: Any) -> None:
        for i in ids:
            forge.updated[i] = START + timedelta(days=5)

    return change


def _delete(*ids: int) -> Any:
    def change(forge: Any) -> None:
        for i in ids:
            del forge.updated[i]

    return change


@pytest.mark.parametrize("after_run", [1, 2, 3])
@pytest.mark.parametrize(
    ("change", "gone"),
    [
        (_edit(1), set()),
        (_edit(1, 2), set()),
        (_delete(2), {2}),
        (_edit(1, 2, 3, 4, 5, 6, 7), set()),
    ],
    ids=["one-edit", "two-edits", "a-deletion", "more-edits-than-a-page-goes-back"],
)
async def test_a_comment_that_leaves_the_block_between_two_runs_hides_no_other(
    after_run: int, change: Any, gone: set[int]
) -> None:
    """#557: comments at one second, one call a run. An edit (or a deletion) of a comment the read already passed
    moves every later comment of the block up a place, and the page the next run asked for by its number started
    one comment later: the comment that moved up onto the page already read was never read, and the cursor then
    moved past it for good (the review's probe: comment 1 edited between runs 2 and 3 read [1, 2, 3, 4, 6, 7, 8, 9]).
    Each page now starts on comments read already and goes on after the newest of them still there."""
    forge = Comments({i: 1 for i in range(1, TIED + 1)})
    read, places = await _runs_changing(forge, calls=1, runs=40, change=change, after_run=after_run)
    expected = set(range(1, TIED + 1)) - gone
    assert read >= expected, f"after run {after_run}: comments never read: {sorted(expected - read)}"
    assert None in places, "no pass finished"


async def test_a_place_lost_with_its_anchors_steps_back_to_the_blocks_start() -> None:
    """The anchors themselves edited away (or more comments left than a page goes back): where the read stood is
    unknown, so it steps back, here to the block's first page, says so, and still reads every one."""
    forge = Comments({i: 1 for i in range(1, TIED + 1)})
    read: set[int] = set()
    cursor, place = START + timedelta(days=1), None
    with capture_logs() as logs:
        for run in range(1, 40):
            reader = _reader(forge, cursor, place)
            await _run(reader, 1)
            read |= {int(e[0].split(":")[1]) for e in reader.batch.events}
            cursor = reader.batch.comments_since or cursor
            place = None if reader.place is None else reader.place.to_json()
            if run == 2:
                assert place is not None and place["tied_anchors"], place
                _edit(*place["tied_anchors"])(forge)
    assert read == set(range(1, TIED + 1)), f"comments never read: {sorted(set(range(1, TIED + 1)) - read)}"
    assert [
        e for e in logs if e["event"] in ("slop_meter_tied_block_restarted", "slop_meter_tied_block_stepped_back")
    ], "the step back is not logged"


class Shuffling:
    """A forge answering every page among tied comments with comments it never answered before: no page ever holds
    the last ones read, so the reader cannot find its place again."""

    def __init__(self) -> None:
        self.calls, self.next_id = 0, 1

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        self.calls += 1
        if not path.endswith("/issues/comments"):
            return FakeResponse(200, [])
        at = (START + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        size = min(int((params or {}).get("per_page", PAGE)), PAGE)
        ids = range(self.next_id, self.next_id + size)
        self.next_id += size
        items = [{"id": i, "user": {"login": "ana"}, "created_at": at, "updated_at": at} for i in ids]
        return FakeResponse(200, items, next_url=f"{FORGE}{path}?page=2")


async def test_a_place_lost_twice_in_one_run_is_cut_short() -> None:
    """Starting the block over is bounded too, so a forge that never shows the last comments read cannot spend a
    run's calls on it (#552): the second loss in a run ends the listing, cut short and logged."""
    forge = Shuffling()
    reader = EventReader(
        forge,
        REPO,
        comments_since=START + timedelta(days=1),
        review_comments_since=None,
        reviews_since=None,
        known_logins=["ana"],
        **CAPS,
    )
    with capture_logs() as logs:
        await _run(reader, 50)
    assert forge.calls <= 6 and reader.batch.more, f"{forge.calls} calls, cut short {reader.batch.more}"
    stopped = [e["reason"] for e in logs if e["event"] == "slop_meter_tied_pages_stopped"]
    assert stopped == ["lost its place twice"], stopped


def test_each_page_starts_on_read_comments_and_goes_past_them() -> None:
    """At a hundred a page, every offset a read reaches has a page that starts on comments read, at least
    TIED_OVERLAP of them (all of them before that many), and brings new ones; checked across the first ten thousand
    offsets and a few far out."""
    from embry0.services.slop_meter.events import tied_window

    events.PER_PAGE = 100  # the autouse fixture's patch is undone after the test
    for offset in [*range(1, 10_001), 123_456, 999_999, 1_000_000]:
        page, size, start = tied_window(offset)
        assert (page - 1) * size == start and start < offset < start + size and 1 <= size <= 100, (offset, page, size)
        assert offset - start >= min(events.TIED_OVERLAP, offset), f"{offset}: the page goes back too few: {start}"


# ---- a block followed by later comments, a place found again only from comments still in the block ------------------


class Block:
    """Issue comments as GitHub lists them: updated since ``since`` (inclusive), oldest update first and, among one
    update, by id; page ``n`` of ``per_page`` (up to ``most``) is items ``(n - 1) * size`` on. ``tied`` comments share
    the block's second, and ``later`` comments follow it a day apart, so a page that reaches the block's end holds them
    too, and a comment edited away moves among them. ``before[n]`` changes the comments before the ``n``th call."""

    def __init__(self, tied: int, later: int = 2, *, most: int = 100) -> None:
        self.updated = {i: START + timedelta(days=1) for i in range(1, tied + 1)}
        self.updated.update({1000 + k: START + timedelta(days=2 + k) for k in range(later)})
        self.most, self.calls = most, 0
        self.before: dict[int, Any] = {}

    async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
        params = params or {}
        if not path.endswith("/issues/comments"):
            return FakeResponse(200, [])
        self.calls += 1
        change = self.before.pop(self.calls, None)
        if change is not None:
            change(self)
        page, since = int(params.get("page", 1)), params.get("since", "")
        size = min(int(params.get("per_page", 30)), self.most)
        stamp = {i: at.strftime("%Y-%m-%dT%H:%M:%SZ") for i, at in self.updated.items()}
        listing = sorted((stamp[i], i) for i in stamp if stamp[i] >= since)
        chunk = listing[(page - 1) * size : page * size]
        items = [{"id": i, "user": {"login": f"user{i}"}, "created_at": at, "updated_at": at} for at, i in chunk]
        nxt = f"{FORGE}{path}?page={page + 1}" if page * size < len(listing) else None
        return FakeResponse(200, items, next_url=nxt)


async def _over_runs(forge: Block, *, calls: int, runs: int, after: Any = None) -> tuple[set[int], list[Any]]:
    """Every comment read over ``runs`` runs of ``calls`` calls and then one run with calls enough to finish, each run
    starting from what the loop stores; ``after(forge, run, place)`` runs after each of the first ``runs``. Returns the
    comments read and the tied-block log lines."""
    read: set[int] = set()
    cursor, place = START, None
    with capture_logs() as logs:
        for run in range(1, runs + 2):
            reader = _reader(forge, cursor, place)
            await _run(reader, calls if run <= runs else 100_000)
            read |= {int(e[0].split(":")[1]) for e in reader.batch.events}
            cursor = reader.batch.comments_since or cursor
            place = None if reader.place is None else reader.place.to_json()
            assert place is None or ReaderPlace.from_json(place, **CAPS) is not None, f"a stored place loads: {place}"
            if after is not None and run <= runs:
                after(forge, run, place)
    return read, [e for e in logs if e["event"].startswith("slop_meter_tied")]


def _gone(*ids: int, edit: bool = True) -> Any:
    def change(forge: Block) -> None:
        for i in ids:
            if edit and i in forge.updated:
                forge.updated[i] = START + timedelta(days=30)
            else:
                forge.updated.pop(i, None)

    return change


def _once(run: int, change: Any) -> Any:
    def after(forge: Block, number: int, place: Any) -> None:
        if number == run:
            change(forge)

    return after


@pytest.fixture
def hundred_a_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "PER_PAGE", 100)


@pytest.mark.usefixtures("hundred_a_page")
async def test_an_anchor_edited_onto_the_page_is_not_the_place() -> None:
    """The review's case: 250 comments at one second, then two later ones, one call a run; comment 180, the last read,
    edited after run 3. Run 4 asked for page 3 of 85 (171 to 250, the two later ones, then 180, moved to the end) and
    took 180 there for the place: 181 to 250 and the two later comments were never read, and the cursor moved past
    them (main left out one). An anchor counts only while it still has the block's update."""
    forge = Block(250)
    read, _logs = await _over_runs(forge, calls=1, runs=30, after=_once(3, _gone(180)))
    missing = sorted(set(forge.updated) - read)
    assert not missing, f"comments never read: {len(missing)}, {missing[:8]}"


@pytest.mark.usefixtures("hundred_a_page")
@pytest.mark.parametrize("edit", [True, False], ids=["edited", "deleted"])
async def test_a_place_lost_on_a_page_past_the_block_does_not_move_the_cursor(edit: bool) -> None:
    """The review's case: 250 at one second, eleven of the comments read edited away (or deleted) after run 3, more
    than a page goes back. Run 4's page (182 to 250 and the later ones) held no anchor, so the place was lost, but the
    page moved the cursor past 181 all the same, and nothing went back for it. A lost place never moves the cursor."""
    forge = Block(250)
    read, _logs = await _over_runs(forge, calls=1, runs=30, after=_once(3, _gone(*range(1, 12), edit=edit)))
    missing = sorted(set(forge.updated) - read)
    assert not missing, f"comments never read: {len(missing)}, {missing[:8]}"


@pytest.mark.usefixtures("hundred_a_page")
async def test_no_comment_is_left_out_across_blocks_departures_and_runs() -> None:
    """The review's grid, with comments after the block: edits and deletions of the first comments read, and edits of
    the newest anchor or of all three, after run 1, 2 or 3, for blocks of 105 to 500; then drained."""
    cases: list[str] = []
    for tied in (105, 150, 190, 250, 300, 500):
        for run in (1, 2, 3):
            changes: dict[str, Any] = {
                f"edit {d}": _once(run, _gone(*range(1, d + 1))) for d in (1, 10, 11, 30, 100, 150) if d < tied
            }
            changes.update(
                {f"delete {d}": _once(run, _gone(*range(1, d + 1), edit=False)) for d in (1, 11, 150) if d < tied}
            )

            def anchors(newest: bool, at: int = run) -> Any:
                def after(forge: Block, number: int, place: Any) -> None:
                    if number == at and place is not None:
                        ids = place.get("tied_anchors") or []
                        _gone(*(ids[-1:] if newest else ids))(forge)

                return after

            changes.update({"edit the newest anchor": anchors(True), "edit the anchors": anchors(False)})
            for name, after in changes.items():
                forge = Block(tied)
                read, _logs = await _over_runs(forge, calls=1, runs=12, after=after)
                missing = sorted(set(forge.updated) - read)
                if missing:
                    cases.append(f"{tied} tied, {name} after run {run}: {len(missing)} never read {missing[:4]}")
    assert not cases, f"{len(cases)} cases leave comments out: {cases[:6]}"


@pytest.mark.usefixtures("hundred_a_page")
@pytest.mark.parametrize(
    ("after_run", "deleted", "back"), [(7, 150, [100, 200]), (9, 350, [100, 200, 400])], ids=["150", "350"]
)
async def test_a_place_lost_deep_in_a_block_steps_back_rather_than_starting_over(
    after_run: int, deleted: int, back: list[int]
) -> None:
    """A thousand at one second; 150 (or 350) of the comments read deleted after run 7 (or 9), more than a page goes
    back. The read steps back a page from where it stood (no anchor there: they moved up), then twice as far as the
    last step, and so on, finds the newest anchor, and goes on from it: a few calls, not a reading of the block again
    from its first page. With 350 gone, a step one page further each time would look at 300 back before 400."""
    forge = Block(1000)
    read, logs = await _over_runs(
        forge, calls=1, runs=20, after=_once(after_run, _gone(*range(1, deleted + 1), edit=False))
    )
    assert not sorted(set(forge.updated) - read), f"comments never read: {sorted(set(forge.updated) - read)[:8]}"
    steps = [
        (e["event"].removeprefix("slop_meter_tied_block_"), e["read"], e.get("to"))
        for e in logs
        if "block" in e["event"]
    ]
    stood = steps[0][1] if steps else 0
    assert stood > back[-1] and steps == [("stepped_back", stood, stood - b) for b in back], (
        f"one page back, then twice as far each time, from where the read stood: {steps}"
    )
    unchanged = Block(1000)
    await _over_runs(unchanged, calls=1, runs=20)
    assert forge.calls <= unchanged.calls + len(back), (
        f"{forge.calls} calls, against {unchanged.calls} with nothing deleted: the loss cost more than its steps back"
    )


@pytest.mark.usefixtures("hundred_a_page")
@pytest.mark.parametrize(("call", "deleted"), [(4, 38), (5, 12), (6, 150)])
async def test_a_place_lost_within_a_run_is_found_again_in_it(call: int, deleted: int) -> None:
    """A loss between two pages of one run: the pages after the place is found again bring comments the run saw
    before the loss, which are not the same page answered again (counted so, the run stopped there, "no comment not
    read already", and the next pass read the block again from its start). The run reads the block through."""
    forge = Block(1000)
    forge.before[call] = _gone(*range(1, deleted + 1), edit=False)
    reader = EventReader(
        forge,
        REPO,
        comments_since=START,
        review_comments_since=None,
        reviews_since=None,
        # Their authors are known, so no login lookup (a cap of 0 here) marks the read as cut short.
        known_logins=[f"user{i}" for i in forge.updated],
        **CAPS,
    )
    with capture_logs() as logs:
        await _run(reader, 40)
    read = {int(e[0].split(":")[1]) for e in reader.batch.events}
    stopped = [e["reason"] for e in logs if e["event"] == "slop_meter_tied_pages_stopped"]
    assert [e for e in logs if e["event"] == "slop_meter_tied_block_stepped_back"], "the run never lost its place"
    assert (sorted(set(forge.updated) - read), stopped, reader.batch.more) == ([], [], False), (
        f"never read {sorted(set(forge.updated) - read)[:8]}, stopped {stopped}, cut short {reader.batch.more}"
    )


@pytest.mark.usefixtures("hundred_a_page")
async def test_anchors_edited_after_every_run_leave_no_comment_out() -> None:
    """The comments a place names edited away after every one of 40 runs: the place is lost every run and found again
    only at the block's start. That is slow, but no comment is left out once the edits stop."""

    def every_run(forge: Block, number: int, place: Any) -> None:
        if place is not None:
            _gone(*(place.get("tied_anchors") or []))(forge)

    forge = Block(400)
    read, logs = await _over_runs(forge, calls=1, runs=40, after=every_run)
    assert len([e for e in logs if e["event"] == "slop_meter_tied_block_restarted"]) >= 10, "the place was not lost"
    assert not sorted(set(forge.updated) - read), f"comments never read: {sorted(set(forge.updated) - read)[:8]}"


@pytest.mark.usefixtures("hundred_a_page")
async def test_a_page_the_forge_refuses_ends_the_listing_rather_than_stepping_back() -> None:
    """A refusal is no answer about where the read stands: the listing ends there, as before, without a step back."""

    class Refusing(Block):
        async def get(self, path: str, *, repo: str, params: dict[str, Any] | None = None) -> FakeResponse:
            if path.endswith("/issues/comments") and int((params or {}).get("page", 1)) > 1:
                self.calls += 1
                return FakeResponse(422, {"message": "no"})
            return await super().get(path, repo=repo, params=params)

    forge = Refusing(300)
    reader = _reader(forge, START, None)
    with capture_logs() as logs:
        await _run(reader, 20)
    tied = [e["event"] for e in logs if e["event"].startswith("slop_meter_tied_block")]
    # The first page from the cursor, that page again from the update it moved the cursor to, and the refused one.
    assert (forge.calls, tied) == (3, []), f"{forge.calls} calls, {tied}: a refused page was taken for a lost place"
