"""A key's repository allowlist applied to offered work, on every path that reads it.

A key whose allowlist does not cover a repository reads none of that
repository's offered work: its quests on offer, proposals, leads, asks and
hand-offs, as it reads none of its quests to review. A list leaves it out;
reading one item answers exactly as an item that does not exist does. A key
without an allowlist reads all of it, as before. Every route that serves
offered work is held to this by ``test_offered_work_inventory.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from httpx import ASGITransport

from embry0.mcp_server import server
from embry0.mcp_server.client import Embry0APIError, Embry0Client
from tests.fixtures.offered_work import (
    COVERED,
    JOBS,
    LIMITED,
    OPEN,
    PROPOSALS,
    RECIPIENTS,
    REPO_OF,
    UNCOVERED,
    get,
    offered_work_app,
)
from tests.unit.api.v1.test_asks_api import ask


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    with offered_work_app(monkeypatch) as built:
        yield built


def _repos(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r["repo"]) for r in rows}


#: Each list of offered work: its URL, its query, and the repositories of the offered work it lists.
LISTS: dict[str, tuple[str, dict[str, Any], Callable[[Any], set[str]]]] = {
    "decisions": (
        "/api/v1/decisions",
        {},
        lambda body: _repos([d for d in body["decisions"] if d["stage"] == "offered"]),
    ),
    "proposals": ("/api/v1/proposals", {"include_held": "true"}, lambda body: _repos(body["proposals"])),
    "asks-due": ("/api/v1/asks/due", {}, lambda body: _repos([a["decision"] for a in body["asks"]])),
    "world-quests": ("/api/v1/world", {}, lambda body: _repos(body["quests"])),
    "leads": ("/api/v1/leads", {}, lambda body: _repos(body["leads"])),
    "handoffs-all": ("/api/v1/handoffs", {"for": "all"}, lambda body: _repos(body["handoffs"])),
    "handoffs-mine": ("/api/v1/handoffs", {}, lambda body: _repos(body["handoffs"])),
    "asks": ("/api/v1/asks", {}, lambda body: {REPO_OF[a["decision_id"]] for a in body["asks"]}),
    "decisions-reviews": (
        "/api/v1/decisions",
        {},
        lambda body: _repos([d for d in body["decisions"] if d["stage"] == "done"]),
    ),
    "world-parties": ("/api/v1/world", {}, lambda body: _repos(body["parties"])),
    "world-inbox": ("/api/v1/world", {}, lambda body: _repos(body["inbox"])),
    "world-loot": ("/api/v1/world", {}, lambda body: _repos(body["loot"])),
    "world-regions": ("/api/v1/world", {}, lambda body: _repos(body["regions"])),
    "world-frontier": ("/api/v1/world", {}, lambda body: set(body["frontier"])),
    "world-dungeons": ("/api/v1/world", {}, lambda body: _repos(body["dungeons"])),
    "world-recent": ("/api/v1/world", {}, lambda body: _repos(body["recent"])),
    "character-quest-log": ("/api/v1/world/characters/developer", {}, lambda body: _repos(body["quest_log"])),
    "progression-diaries": ("/api/v1/progression", {}, lambda body: _repos(body["diaries"])),
    "progression-recent": ("/api/v1/progression", {}, lambda body: _repos(body["recent"])),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", LISTS)
async def test_a_list_of_offered_work_shows_a_key_only_the_repositories_its_allowlist_covers(
    app: Any, name: str
) -> None:
    url, params, repos_of = LISTS[name]
    limited = await get(app, url, LIMITED, **params)
    assert limited.status_code == 200, f"{url} through the limited key: {limited.status_code} {limited.text}"
    assert repos_of(limited.json()) == {COVERED}, (
        f"{url} lists to a key covering only {COVERED} that repository's work and no other's: "
        f"{sorted(repos_of(limited.json()))}"
    )
    assert UNCOVERED not in limited.text and RECIPIENTS[UNCOVERED] not in limited.text, (
        f"{url} names {UNCOVERED}, and the person asked about it, nowhere in its answer to the limited key"
    )

    open_ = await get(app, url, OPEN, **params)
    assert open_.status_code == 200, open_.text
    assert repos_of(open_.json()) == {COVERED, UNCOVERED}, (
        f"a key without an allowlist still reads every repository's offered work: {sorted(repos_of(open_.json()))}"
    )


#: Each read of one item, by its id: the URL of each repository's item, and of an item that does not exist.
READS: dict[str, tuple[Callable[[str], str], str]] = {
    "proposal": (lambda repo: f"/api/v1/proposals/{PROPOSALS[repo]}", "/api/v1/proposals/issue:no-such-1"),
    "decision-on-offer": (
        lambda repo: f"/api/v1/decisions/offer:{PROPOSALS[repo]}",
        "/api/v1/decisions/offer:issue:no-such-1",
    ),
    "decision-to-review": (lambda repo: f"/api/v1/decisions/done:{JOBS[repo]}", "/api/v1/decisions/done:job-no-such"),
    "quest-scroll": (lambda repo: f"/api/v1/world/quests/{JOBS[repo]}", "/api/v1/world/quests/job-no-such"),
}


def as_missing(response: Any, item: str) -> tuple[int, Any]:
    """A response with the item's own id, which a 404 may repeat, put back as a missing item's would read."""
    return response.status_code, json.loads(response.text.replace(item, "<item>"))


@pytest.mark.asyncio
@pytest.mark.parametrize("name", READS)
async def test_reading_an_item_a_key_does_not_cover_answers_as_a_missing_item_does(app: Any, name: str) -> None:
    url, missing_url = READS[name]
    missing = await get(app, missing_url, LIMITED)
    uncovered = await get(app, url(UNCOVERED), LIMITED)
    assert missing.status_code == 404, f"the fixture's missing item: {missing.status_code} {missing.text}"
    missing_id = missing_url.rsplit("/", 1)[1].split(":", 1)[-1]
    uncovered_id = url(UNCOVERED).rsplit("/", 1)[1].split(":", 1)[-1]
    assert as_missing(uncovered, uncovered_id) == as_missing(missing, missing_id), (
        f"{url(UNCOVERED)} through a key that does not cover {UNCOVERED} answers as a missing item does "
        f"({missing.status_code} {missing.json()}), not {uncovered.status_code} {uncovered.text[:200]}"
    )

    covered = await get(app, url(COVERED), LIMITED)
    assert covered.status_code == 200 and COVERED in covered.text, (
        f"the limited key reads its own repository's item: {covered.status_code} {covered.text[:200]}"
    )
    open_ = await get(app, url(UNCOVERED), OPEN)
    assert open_.status_code == 200 and UNCOVERED in open_.text, (
        f"a key without an allowlist reads every repository's item: {open_.status_code} {open_.text[:200]}"
    )


@pytest.fixture
def mcp_as(app: Any, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Point the MCP tools at the app, with ``token`` as their key."""

    def use(token: str) -> None:
        client = Embry0Client("http://test", api_key=token, transport=ASGITransport(app=app))
        monkeypatch.setattr(server, "_client", client)

    return use


@pytest.mark.asyncio
async def test_the_mcp_proposal_tools_read_offered_work_through_the_key_s_allowlist(
    mcp_as: Callable[[str], None],
) -> None:
    mcp_as(LIMITED)
    listed = await server.proposal_list()
    assert _repos(listed["proposals"]) == {COVERED}, f"proposal_list through the limited key: {listed}"
    with pytest.raises(Embry0APIError) as refused:
        await server.proposal_get(PROPOSALS[UNCOVERED])
    with pytest.raises(Embry0APIError) as missing:
        await server.proposal_get("issue:no-such-1")
    assert (refused.value.status_code, refused.value.detail) == (missing.value.status_code, missing.value.detail), (
        f"proposal_get of an uncovered repository's proposal answers as a missing one: {refused.value}"
    )

    mcp_as(OPEN)
    assert _repos((await server.proposal_list())["proposals"]) == {COVERED, UNCOVERED}
    assert (await server.proposal_get(PROPOSALS[UNCOVERED]))["repo"] == UNCOVERED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "params", "rows"), [("/api/v1/leads", {}, "leads"), ("/api/v1/handoffs", {"for": "all"}, "handoffs")]
)
async def test_a_page_of_offered_work_is_narrowed_to_the_key_s_repositories_before_it_is_cut(
    app: Any, url: str, params: dict[str, str], rows: str
) -> None:
    """These lists page in SQL; the newest row is the uncovered repository's, so a page of one cut before it is
    narrowed would hold nothing the key may read."""
    page = await get(app, url, LIMITED, limit=1, **params)
    assert page.status_code == 200, page.text
    assert _repos(page.json()[rows]) == {COVERED}, f"a page of one through the limited key: {page.json()[rows]}"


@pytest.mark.asyncio
async def test_asks_about_closed_decisions_are_listed_by_the_repository_read_back_for_them(app: Any) -> None:
    """A closed decision is in no open list: its repository is read back from the row it came from. A finding no
    one answered has none to read back, so only a key that covers every repository lists its ask."""
    asks = app.state.asks_repo
    closed = {"done:job-old-web": COVERED, "done:job-old-gizmos": UNCOVERED}
    asks.repos.update(closed)
    for decision_id in [*closed, "offer:finding:never-answered"]:
        asks.rows.append(ask(f"ask-{decision_id}", decision_id, "ada@example.com", state="answered"))
    limited = {a["decision_id"] for a in (await get(app, "/api/v1/asks", LIMITED)).json()["asks"]}
    everyone = {a["decision_id"] for a in (await get(app, "/api/v1/asks", OPEN)).json()["asks"]}
    assert {"done:job-old-web"} <= limited and not limited & {"done:job-old-gizmos", "offer:finding:never-answered"}, (
        f"the limited key lists the ask about acme/web's closed decision and neither other: {sorted(limited)}"
    )
    assert {*closed, "offer:finding:never-answered"} <= everyone, f"a key without an allowlist lists all: {everyone}"
    assert all("decision_repo" not in a for a in (await get(app, "/api/v1/asks", LIMITED)).json()["asks"]), (
        "the answer's shape is unchanged"
    )
