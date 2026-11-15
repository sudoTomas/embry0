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
        {"stage": 0, "pages": 1, "tied_page": 3},
        {"stage": 0, "pages": 10, "tied_page": 11},
        {"stage": 0, "pages": 2, "tied_page": 1},
        {"stage": 0, "pages": 2, "tied_page": True},
        {"stage": 0, "pages": 2, "tied_page": "3"},
    ],
    ids=["pull-listing", "past-the-pages-read", "past-the-cap", "first-page", "bool", "text"],
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
