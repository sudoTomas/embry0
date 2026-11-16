"""Comments that share one update are read on across runs, page by page (#526).

A comment listing goes on from its cursor, the newest update it read. Among
comments that all have the update a page was asked from, the cursor cannot
move, so the listing goes on by page number (#510). That page was not kept
between runs: a run that stopped inside such a block asked for its first pages
again the next run, from the same cursor, and a credential's share smaller
than the block's pages never read past them. The place a run stops at now
keeps that page (``tied_page``), and the next run asks for it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from embry0.services.slop_meter.events import ApiBudget, BudgetSpent, EventReader, ReaderPlace
from tests.unit.services.slop_meter.test_loop import REPO, START, Comments

#: The caps the meter reads with here: ten pages a listing, no reviews or logins.
CAPS = {"max_pages": 10, "max_pulls": 0, "max_logins": 0}
#: Nine comments updated at the same second, two a page: five pages of them.
TIED = 9


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
    """The issue's case: nine comments at one second, 1, 2, 3 or 100 calls a run, over 20 runs."""
    read, every = await _runs(Comments({i: 1 for i in range(1, TIED + 1)}), calls=calls, runs=20)
    assert read == set(range(1, TIED + 1)), f"{calls} call(s) a run: read {len(read)} of {TIED} ({sorted(read)})"
    # The first page asked from the old cursor, then the block's five pages from its update: one call each.
    assert every is not None and every <= -(-6 // calls), f"{calls} call(s) a run took {every} runs to read them"


async def test_the_place_keeps_the_page_a_listing_reached_among_tied_comments() -> None:
    # Two older comments, before the cursor: a page asked for without it would start at them.
    forge = Comments({101: -1, 102: -1, **{i: 1 for i in range(1, TIED + 1)}})
    reader = _reader(forge, START + timedelta(days=1), None)
    await _run(reader, 2)
    place = reader.place
    assert place is not None and place.stage == 0
    assert place.tied_page == 3, f"the page the listing reached is not kept: {place}"
    assert place.next_page is None, "a comment listing's page is its own field, never the pull request listing's"
    assert ReaderPlace.from_json(place.to_json(), **CAPS) == place, "the page does not survive being stored"

    resumed = _reader(forge, reader.batch.comments_since, place.to_json())
    await _run(resumed, 1)
    assert {int(e[0].split(":")[1]) for e in resumed.batch.events} == {5, 6}, "the next run does not go on at page 3"


async def test_the_page_is_dropped_once_the_tied_comments_are_passed() -> None:
    """Five comments share one update, then two later ones: past the block the listing goes on from its cursor."""
    forge = Comments({**{i: 1 for i in range(1, 6)}, 6: 2, 7: 3})
    reader = _reader(forge, START + timedelta(days=1), None)
    await _run(reader, 3)  # pages 1 to 3: comments 1-2, 3-4, then 5 and 6, a later update
    place = reader.place
    assert place is not None and place.stage == 0 and place.tied_page is None, (
        f"a listing past the tied comments still goes on by page number: {place}"
    )
    assert reader.batch.comments_since == START + timedelta(days=2)


async def test_a_place_stored_before_the_page_was_kept_goes_on_from_the_cursor() -> None:
    """A place with no ``tied_page`` (stored before it was kept), even one naming a comment listing's ``next_page``
    as earlier readers wrote it, still loads, and the listing goes on from its cursor at its first page."""
    forge = Comments({i: 1 for i in range(1, TIED + 1)})
    calls: list[dict[str, Any]] = []
    original = forge.get

    async def get(path: str, *, repo: str, params: dict[str, Any] | None = None) -> Any:
        calls.append(dict(params or {}))
        return await original(path, repo=repo, params=params)

    forge.get = get  # type: ignore[method-assign]
    for stored in (
        {"stage": 0, "pages": 2},
        {"stage": 0, "pages": 2, "next_page": 3, "pulls": [], "found": 0, "seen": [], "pulls_left": 0},
    ):
        place = ReaderPlace.from_json(stored, **CAPS)
        assert place is not None and place.tied_page is None, stored
        calls.clear()
        reader = _reader(forge, START + timedelta(days=1), stored)
        await _run(reader, 1)
        assert "page" not in calls[0], f"an earlier place is read as a page among tied comments: {calls[0]}"


@pytest.mark.parametrize(
    "stored",
    [
        {"stage": 2, "pages": 2, "tied_page": 3},
        {"stage": 0, "pages": 2, "tied_page": 1},
        {"stage": 0, "pages": 2, "tied_page": 10_001},
        {"stage": 0, "pages": 2, "tied_page": True},
        {"stage": 0, "pages": 2, "tied_page": "3"},
    ],
    ids=["pull-listing", "first-page", "past-the-bound", "bool", "text"],
)
def test_a_page_no_reader_could_have_stored_is_no_place(stored: dict[str, Any]) -> None:
    assert ReaderPlace.from_json(stored, **CAPS) is None, stored


def test_the_pull_request_listings_place_keeps_its_next_page_and_no_tied_one() -> None:
    """The pull request listing goes on from ``next_page``; a tied page there would make its place no place."""
    stored = ReaderPlace(stage=2, pages=1, next_page=2)
    reader = _reader(Comments({}), START, stored.to_json())
    place = reader.place
    assert place is not None and (place.next_page, place.tied_page) == (2, None), place
    assert ReaderPlace.from_json(place.to_json(), **CAPS) == place, "the pull request listing's place does not load"


def test_a_page_among_tied_comments_past_the_page_cap_is_a_place() -> None:
    """Pages among comments that share one update are not counted against the cap (#538), so a place may name one
    past it, and past the pages it counted."""
    for stored in ({"stage": 0, "pages": 1, "tied_page": 3}, {"stage": 1, "pages": 10, "tied_page": 11}):
        place = ReaderPlace.from_json(stored, **CAPS)
        assert place is not None and place.tied_page == stored["tied_page"], (
            f"a place among tied comments past the pages it counted is refused, so the read starts over: {stored}"
        )


@pytest.mark.parametrize("pages", [1, 2])
@pytest.mark.parametrize("calls", [1, 3, 100])
async def test_a_block_of_comments_with_one_update_longer_than_the_page_cap_is_read_through(
    calls: int, pages: int
) -> None:
    """#538: nine comments at one second, two a page, a page cap of 2. The cap cut the block at its second page
    every pass, and the next pass asked for its first pages again: `read 4 of 9; more=True` for good. A cap of 1
    counts the first page from the cursor, which moves it to that second; the next pass reads the block through."""
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
    """Six comments, each its own update: the cap of 2 cuts the listing after two pages, as before (the second asked
    from the cursor the first moved, so comment 2 again)."""
    reader = EventReader(
        Comments({i: i for i in range(1, 7)}),
        REPO,
        comments_since=START,
        review_comments_since=None,
        reviews_since=None,
        **{**CAPS, "max_pages": 2},
    )
    await _run(reader, 100)
    read = {int(e[0].split(":")[1]) for e in reader.batch.events}
    assert (reader.batch.more, read) == (True, {1, 2, 3}), f"the cap no longer cuts a listing that moves: {read}"


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
    """Fifteen comments at one second, eight pages, a cap of 2 and one call a run: the place each run stores must
    still load (its pages counted stay within the cap), or every run would start the block over."""
    tied = 15
    read: set[int] = set()
    cursor, place = START, None
    caps = {**CAPS, "max_pages": 2}
    forge = Comments({i: 1 for i in range(1, tied + 1)})
    for _run_number in range(40):
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
    assert read == set(range(1, tied + 1)), f"one call a run, 40 runs: read {len(read)} of {tied} ({sorted(read)})"
