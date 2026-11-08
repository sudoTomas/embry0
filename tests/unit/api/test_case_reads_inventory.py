"""Every GET route that serves a healing case reads, to a key with a repository allowlist, only the cases of the
repositories it covers.

On the real app over a real database (:mod:`tests.fixtures.case_reads`), with
a case in each of two repositories, the cases no key with an allowlist reads
(filed on an owner, or on no repository) and a case of the covered repository
whose links cross into the other, every GET route the app serves under
``/api/v1`` is read through an admin key whose list covers one repository and
through an admin key without a list, once with each path parameter naming:

- the uncovered repository's case, and its items as
  :func:`tests.unit.api.test_issue_reads_inventory.uncovered_parameters` names
  them, with the decision and the proposal that offer the case;
- each case no key with an allowlist reads, the rest naming the covered
  repository's items that cross
  (:func:`tests.unit.api.test_issue_reads_inventory.crossing_parameters`);
- the crossing case (:data:`tests.fixtures.case_reads.CROSSING`), the same.

:data:`QUERIES` adds the list of cases with each filter that selects a hidden
one, and another repository's.

A read addressed by a hidden case shows it when its answer is not the one the
route gives for a case that does not exist; a read addressed by the crossing
case, when its answer names anything of the other repository it links to
(:func:`tests.fixtures.case_reads.shows_crossing`); any other read, when its
answer names a hidden case or anything stored for it
(:func:`tests.fixtures.case_reads.shows_hidden_case`).

- No route in :data:`CASE_ROUTES` shows the limited key any of it.
- The routes that show it to the key without a list are exactly
  :data:`CASE_ROUTES` and :data:`ELSEWHERE`. A new route that serves a case
  fails until it applies the rule and is listed with how, or is placed in
  :data:`ELSEWHERE` with the stage that narrows it; a listed route that no
  longer serves it fails until it is taken out.
- Each route that reads one case answers the limited key on each hidden case
  byte for byte as on one that does not exist, that answer is every caller's
  for one that does not exist, and the limited key still reads its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Request

from embry0.storage.database import DatabasePool
from tests.fixtures.case_reads import (
    CASE,
    CROSSING,
    HIDDEN_CASES,
    case_reads_app,
    missing_like,
    shows_crossing,
    shows_hidden_case,
)
from tests.fixtures.issue_reads import same_answer
from tests.fixtures.repository_writes import COVERED, OWNER, UNCOVERED, call
from tests.fixtures.routes import app_routes
from tests.unit.api.test_issue_reads_inventory import crossing_parameters, uncovered_parameters
from tests.unit.api.test_job_reads_inventory import NO_ANSWER_HERE, STREAMS, address, get_routes

pytestmark = pytest.mark.requires_postgres

#: Every GET route that serves a case, a case's part, a list of cases or something that names one, and how it applies
#: a key's allowlist.
CASE_ROUTES: dict[str, str] = {
    "/api/v1/cases": (
        "lists the cases of the repositories the key covers, narrowed before the page, the total and the cursor, "
        "with every filter; each row counts and names only the jobs and pull requests of those repositories, and "
        "no issue or case the key does not read"
    ),
    "/api/v1/cases/{case_id}": (
        "reads another repository's case, or one filed on an owner or on no repository, as a missing one (404); "
        "lists only the jobs, pull requests and history of the repositories the key covers, and names no issue or "
        "case the key does not read"
    ),
    "/api/v1/orgs/{owner}/needs": "names no case the key does not read (a need's case_id is null)",
    "/api/v1/merge-approvals": (
        "lists the approvals of the repositories the key covers (#318), each naming no case the key does not read "
        "(case_id null)"
    ),
    "/api/v1/merge-approvals/{approval_id}": (
        "reads another repository's approval as a missing one (#318), and names no case the key does not read"
    ),
    # Earlier stages: the findings offered, and the decisions and asks about them.
    "/api/v1/proposals": "lists the findings offered in the repositories the key covers, since #297",
    "/api/v1/proposals/{proposal_id}": "reads another repository's finding as a missing one, since #297",
    "/api/v1/decisions": "lists the findings offered in the repositories the key covers, since #297",
    "/api/v1/decisions/{decision_id:path}": "reads another repository's finding as a missing one, since #297",
    "/api/v1/asks": "lists the asks about the repositories the key covers, since #297",
    "/api/v1/world": "shows the findings offered in the repositories the key covers, since #297",
}

#: GET routes that show a case of another repository inside something else, narrowed with that thing as a whole in a
#: stage of their own, and which.
ELSEWHERE: dict[str, str] = {
    "/api/v1/audit": "a case's audit rows among every audit row: the audit reads",
}

#: Queries the list of cases is also read with, so the walk reaches each hidden case through each filter that selects
#: it, and another repository's.
QUERIES: dict[str, tuple[str, ...]] = {
    "/api/v1/cases": (
        "?limit=200",
        "?awaiting_decision=true&limit=200",
        "?open_only=true&limit=200",
        "?origin=org",
        "?origin=alert",
        "?stage=deployed",
        f"?repo={UNCOVERED}",
        f"?repo={OWNER[COVERED]}",
        "?repo=",
    ),
}

_SECONDS = 10.0


def case_parameters(values: Mapping[str, Mapping[str, str]], case_id: str) -> dict[str, str]:
    """Each path parameter's value: ``case_id`` for a case and for the decision and the proposal that offer it; for a
    case the key does not read but its crossing one, the uncovered repository's items, else the covered repository's
    items that cross."""
    base = uncovered_parameters(values) if case_id == CASE[UNCOVERED] else crossing_parameters(values)
    owner = OWNER[UNCOVERED] if case_id in (CASE[UNCOVERED], f"hc-org-{OWNER[UNCOVERED]}") else OWNER[COVERED]
    return {
        **base,
        "case_id": case_id,
        "decision_id": f"offer:case:{case_id}",
        "proposal_id": f"case:{case_id}",
        "owner": owner,
    }


def _names(case_id: str) -> dict[str, str]:
    return {case_id: "<case>"}


async def _read(app: FastAPI, url: str, token: str) -> httpx.Response:
    try:
        response = await asyncio.wait_for(call(app, "GET", url, token), _SECONDS)
    except TimeoutError:
        pytest.fail(f"GET {url} did not answer in {_SECONDS}s: it measures nothing; list it in NO_ANSWER_HERE with why")
    assert response.status_code != 429, f"GET {url} was rate limited: the walk would measure nothing past it"
    return response


def _text(response: httpx.Response) -> str:
    return response.text + str(response.headers)


async def serving(app: FastAPI, values: Mapping[str, Mapping[str, str]], token: str) -> dict[str, tuple[str, int]]:
    """Each GET route whose answer to ``token`` shows a case the limited key does not read, or what the crossing case
    links to elsewhere, with the first URL that did and its status."""
    found: dict[str, tuple[str, int]] = {}
    for path in get_routes(app):
        for case_id in (*HIDDEN_CASES, CROSSING):
            url = address(path, case_parameters(values, case_id))
            response = await _read(app, url, token)
            if case_id in url and case_id != CROSSING:
                missing_id = missing_like(case_id)
                missing = await _read(app, address(path, case_parameters(values, missing_id)), token)
                shows = same_answer(response, _names(case_id), missing, _names(missing_id)) is not None
            elif case_id in url:
                shows = shows_crossing(_text(response))
            else:
                shows = shows_hidden_case(_text(response))
            if shows:
                found.setdefault(path, (url, response.status_code))
        for query in QUERIES.get(path, ()):
            response = await _read(app, f"{path}{query}", token)
            if shows_crossing(_text(response)):
                found.setdefault(path, (f"{path}{query}", response.status_code))
    return found


async def test_the_routes_that_serve_a_case_show_a_key_only_the_cases_its_allowlist_covers(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, values):
        served = {r.path for r in app_routes(app) if r.methods and "GET" in r.methods}
        limited = await serving(app, values, keys.limited)
        everyone = await serving(app, values, keys.open)
    stale = sorted((set(CASE_ROUTES) | set(ELSEWHERE) | set(STREAMS) | set(NO_ANSWER_HERE) | set(QUERIES)) - served)
    assert stale == [], f"listed here but not served: {stale}"
    shown = {path: seen for path, seen in limited.items() if path not in ELSEWHERE}
    assert shown == {}, (
        f"routes that showed a key covering only {COVERED} a case of {UNCOVERED}, of an owner or of no repository, "
        f"or what its own case links to there: {shown}. A route that reads one case answers such a case as a missing "
        "one (embry0.api.case_allowlist), and a list narrows its query to the repositories the key covers"
    )
    unplaced = sorted(set(everyone) - set(CASE_ROUTES) - set(ELSEWHERE))
    assert unplaced == [], (
        f"routes that serve a case and are not listed: {unplaced}. Apply the key's allowlist and list each in "
        "CASE_ROUTES with how, or place it in ELSEWHERE with the stage that narrows it"
    )
    quiet = sorted((set(CASE_ROUTES) | set(ELSEWHERE)) - set(everyone))
    assert quiet == [], (
        f"listed routes that did not show a hidden case to a key without an allowlist: {quiet}: the inventory no "
        "longer measures them; seed what they read in tests/fixtures/case_reads.py, or take them out"
    )


@pytest.mark.parametrize("query", QUERIES["/api/v1/cases"])
async def test_each_query_of_the_list_reaches_a_case_the_limited_key_does_not_read(
    query: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each query the walk reads the list with selects, for a key without a list, a case the limited key does not read
    or what the crossing case links to elsewhere, so it measures something; the limited key reads none of it."""
    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        everyone = await _read(app, f"/api/v1/cases{query}", keys.open)
        limited = await _read(app, f"/api/v1/cases{query}", keys.limited)
    assert everyone.status_code == 200 and shows_crossing(everyone.text), (
        f"{query} selects nothing the limited key may not read, so it measures nothing: {everyone.text[:300]}"
    )
    assert limited.status_code == 200 and not shows_crossing(limited.text), f"{query}: {limited.text[:300]}"


#: The GET routes that read one case.
ONE_CASE = [path for path in CASE_ROUTES if "{case_id}" in path]


@pytest.mark.parametrize("hidden", HIDDEN_CASES)
@pytest.mark.parametrize("path", ONE_CASE)
async def test_a_case_the_key_does_not_read_answers_as_one_that_does_not_exist(
    path: str, hidden: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, values):
        missing_id = missing_like(hidden)
        hidden_url = address(path, case_parameters(values, hidden))
        missing_url = address(path, case_parameters(values, missing_id))
        own_url = address(path, case_parameters(values, CASE[COVERED]))
        uncovered = await call(app, "GET", hidden_url, keys.limited)
        missing = await call(app, "GET", missing_url, keys.limited)
        missing_to_everyone = await call(app, "GET", missing_url, keys.open)
        reached = await call(app, "GET", hidden_url, keys.open)
        own_read = await call(app, "GET", own_url, keys.limited)
    differ = same_answer(uncovered, _names(hidden), missing, _names(missing_id))
    assert differ is None, f"{path} {hidden}: the limited key reads the case as a missing one: {differ}"
    differ = same_answer(missing, _names(missing_id), missing_to_everyone, _names(missing_id))
    assert differ is None, f"{path}: the limited key's answer for a missing case is everyone's: {differ}"
    assert reached.status_code == 200 and shows_hidden_case(reached.text), (
        f"{path} {hidden}: a key without an allowlist reads the case, so the check measures something: "
        f"{reached.status_code} {reached.text[:200]}"
    )
    assert own_read.status_code == 200 and CASE[COVERED] in own_read.text, (
        f"{path}: the limited key reads its own repository's case: {own_read.status_code} {own_read.text[:200]}"
    )


async def test_the_inventory_finds_a_new_route_that_serves_a_case_without_the_rule(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not quiet by construction: a route serving a case's row as it is stored, and one listing every case, are found
    and reported."""
    async with case_reads_app(db_with_migrations, monkeypatch) as (app, keys, values):
        extra = APIRouter()

        @extra.get("/cases/{case_id}/raw-for-a-test")
        async def raw(case_id: str, request: Request) -> dict[str, Any]:
            return dict(await request.app.state.healing_cases_repo.get(case_id) or {})

        @extra.get("/every-case-for-a-test")
        async def every(request: Request) -> list[str]:
            rows, _total = await request.app.state.healing_cases_repo.list_page(limit=200)
            return [str(row["id"]) for row in rows]

        app.include_router(extra, prefix="/api/v1")
        found = await serving(app, values, keys.limited)
    assert {path for path in found if path not in ELSEWHERE} == {
        "/api/v1/cases/{case_id}/raw-for-a-test",
        "/api/v1/every-case-for-a-test",
    }, f"the walk reports both new routes and no route in CASE_ROUTES: {found}"
