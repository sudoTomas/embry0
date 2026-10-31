"""Every GET route that serves a repository's decisions, its offered work or the game layer's view of its work
applies the key's repository allowlist, and is listed here.

Each GET route the app serves under ``/api/v1`` is read twice against
sources holding such work in two repositories
(:mod:`tests.fixtures.offered_work`): through a key whose allowlist covers
one of them, and through a key without an allowlist. A path parameter that
names a proposal, a decision, a job or a character names the uncovered
repository's, or one with work in it, so a read of one item is tried too:
once with each value a parameter can take.

- No route shows the limited key the uncovered repository's work.
- The routes that show it to the key without an allowlist are exactly
  :data:`OFFERED_WORK_ROUTES`. A new route that serves such work fails
  until it applies the rule and is listed with how; one that stops serving
  it fails until it is taken out.

What this cannot catch: a route that reads such work from somewhere the
fixture does not fill, such as straight from the database. The routes that
act on one item are held to the rule by ``test_offered_work_actions_inventory.py``.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, Request

from embry0.api.v1.proposals import feed
from tests.fixtures.offered_work import (
    JOBS,
    LIMITED,
    OPEN,
    PROPOSALS,
    UNCOVERED,
    get,
    names_uncovered,
    offered_work_app,
)
from tests.fixtures.routes import app_routes

#: Every GET route that serves offered work, and how it applies a key's repository allowlist.
OFFERED_WORK_ROUTES: dict[str, str] = {
    "/api/v1/decisions": "lists the quests on offer and to review in the repositories the key covers",
    "/api/v1/decisions/{decision_id:path}": "reads one quest on offer the key does not cover as a missing one (404)",
    "/api/v1/proposals": "lists the proposals in the repositories the key covers",
    "/api/v1/proposals/{proposal_id}": "reads a proposal the key does not cover as a missing one (404)",
    "/api/v1/asks/due": "hands out the asks about decisions in the repositories the key covers",
    "/api/v1/world": "shows the parties, quests, inbox, loot, regions, dungeons and diaries of the repositories the key covers",
    "/api/v1/leads": "lists the leads of the repositories the key covers",
    "/api/v1/handoffs": "lists the hand-offs of decisions in the repositories the key covers",
    "/api/v1/asks": "lists the asks about decisions in the repositories the key covers",
    "/api/v1/world/characters/{agent_type}": "lists a character's quests in the repositories the key covers",
    "/api/v1/world/quests/{job_id}": "reads a quest of a repository the key does not cover as a missing one (404)",
    "/api/v1/progression": "shows the diaries, and their recent tiers, of the repositories the key covers",
}

#: The values a path parameter is filled with, each tried: the uncovered repository's item where it names one.
PARAMETERS = {
    "proposal_id": [PROPOSALS[UNCOVERED]],
    "decision_id": [f"offer:{PROPOSALS[UNCOVERED]}", f"done:{JOBS[UNCOVERED]}"],
    "job_id": [JOBS[UNCOVERED]],
    "agent_type": ["developer"],
}
_PARAMETER = re.compile(r"\{(\w+)(?::\w+)?\}")
#: Long enough for any route here, which reads only fakes; a route that waits longer shows nothing.
_SECONDS = 10.0


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """The offered-work app, with no way out: no network, no Docker daemon."""

    def no_network(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("the offered-work inventory reaches no network")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    monkeypatch.setenv("DOCKER_HOST", "unix:///nonexistent.sock")
    with offered_work_app(monkeypatch) as built:
        yield built


def _urls(path: str) -> list[str]:
    """``path`` with each value its parameters can take, ``x`` for a parameter that names nothing here."""
    urls = [path]
    for name in _PARAMETER.findall(path):
        urls = [
            _PARAMETER.sub(lambda m, v=value: v if m[1] == name else m[0], url, count=1)
            for url in urls
            for value in PARAMETERS.get(name, ["x"])
        ]
    return urls


async def serving(app: Any, token: str) -> dict[str, int]:
    """Each GET route under ``/api/v1`` whose answer to ``token`` names the uncovered repository's offered work,
    with its status."""
    paths = sorted(
        {r.path for r in app_routes(app) if r.is_api_route and r.methods and "GET" in r.methods}
        & {r.path for r in app_routes(app) if r.path.startswith("/api/v1/")}
    )
    found: dict[str, int] = {}
    for path in paths:
        for url in _urls(path):
            try:
                response = await asyncio.wait_for(get(app, url, token), _SECONDS)
            except TimeoutError:
                continue
            if names_uncovered(response.text):
                found[path] = response.status_code
    return found


@pytest.mark.asyncio
async def test_no_route_shows_a_key_offered_work_its_allowlist_does_not_cover(app: Any) -> None:
    shown = await serving(app, LIMITED)
    assert shown == {}, (
        f"routes that showed a key covering only acme/web the offered work of {UNCOVERED}: {shown}. A route that "
        "lists offered work keeps only what embry0.api.repo_allowlist.key_covers passes; one that reads one item "
        "answers for an uncovered repository as for a missing item"
    )


@pytest.mark.asyncio
async def test_the_routes_that_serve_offered_work_are_exactly_the_listed_ones(app: Any) -> None:
    shown = set(await serving(app, OPEN))
    assert shown - set(OFFERED_WORK_ROUTES) == set(), (
        f"routes that serve offered work and are not listed: {sorted(shown - set(OFFERED_WORK_ROUTES))}. Apply "
        "the key's allowlist (embry0.api.repo_allowlist.key_covers) and list each in OFFERED_WORK_ROUTES with how"
    )
    assert set(OFFERED_WORK_ROUTES) - shown == set(), (
        f"listed routes that did not show offered work to a key without an allowlist: "
        f"{sorted(set(OFFERED_WORK_ROUTES) - shown)}: the inventory no longer measures them"
    )


@pytest.mark.asyncio
async def test_the_inventory_finds_a_new_route_that_skips_the_allowlist(app: Any) -> None:
    """The check is not quiet by construction: a route serving the proposals as they are stored is reported."""
    extra = APIRouter()

    @extra.get("/offered-work-for-a-test")
    async def everything(request: Request) -> list[dict[str, Any]]:
        return [p.to_dict() for p in await feed(request)]

    app.include_router(extra, prefix="/api/v1")
    assert await serving(app, LIMITED) == {"/api/v1/offered-work-for-a-test": 200}
