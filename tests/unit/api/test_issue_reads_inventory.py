"""Every GET route that serves an issue or a merge approval reads, to a key with a repository allowlist, only those of
the repositories it covers.

On the real app over a real database (:mod:`tests.fixtures.issue_reads`),
with an open issue and a merge approval in each of two repositories and
everything the issue routes read stored for each, every GET route the app
serves under ``/api/v1`` is read through an admin key whose list covers one
repository and through an admin key without a list, three times:

- with each path parameter naming the uncovered repository's item (its
  issue, question, job and merge approval; the rest as
  :func:`tests.fixtures.repository_writes.ids` names them);
- with each naming an item of the covered repository whose work crosses
  into the uncovered one (:data:`tests.fixtures.issue_reads.MIXED`, whose
  child, job and question are the other repository's, and
  :data:`tests.fixtures.issue_reads.JOB_HERE`, whose issue and question are);
- the same, with the issue :data:`tests.fixtures.issue_reads.CHILD_HERE`,
  whose parent is the other repository's.

:data:`QUERIES` adds the list of every issue, the children among them.

A read addressed by an uncovered item shows it when its answer is not the
one the route gives for an item that does not exist; any other read, when
its answer names an issue of the uncovered repository, its work or its
merge approval (:func:`tests.fixtures.issue_reads.shows_uncovered_issue`).

- No route in :data:`ISSUE_ROUTES` shows the limited key any of it.
- The routes that show it to the key without a list are exactly
  :data:`ISSUE_ROUTES` and :data:`ELSEWHERE`. A new route that serves an
  issue or a merge approval fails until it applies the rule and is listed
  with how, or is placed in :data:`ELSEWHERE` with the stage that narrows it;
  a listed route that no longer serves it fails until it is taken out.
- Each route that reads one issue or one merge approval answers the limited
  key on the uncovered one byte for byte as on one that does not exist, that
  answer is every caller's for one that does not exist, and the limited key
  still reads its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Request

from embry0.storage.database import DatabasePool
from tests.fixtures.issue_reads import (
    APPROVAL,
    CHILD_HERE,
    INPUT,
    ISSUE,
    JOB_HERE,
    MISSING,
    MISSING_APPROVAL,
    MISSING_INPUT,
    MIXED,
    issue_reads_app,
    same_answer,
    shows_uncovered_approval,
    shows_uncovered_issue,
)
from tests.fixtures.job_reads import JOB
from tests.fixtures.repository_writes import COVERED, UNCOVERED, call
from tests.fixtures.routes import app_routes
from tests.unit.api.test_job_reads_inventory import NO_ANSWER_HERE, STREAMS, address, get_routes, parameters

pytestmark = pytest.mark.requires_postgres

#: Every GET route that serves an issue, an issue's part, a list of issues or a merge approval, and how it applies a
#: key's allowlist.
ISSUE_ROUTES: dict[str, str] = {
    "/api/v1/issues": (
        "lists the issues of the repositories the key covers, narrowed before the page and the total; each one's "
        "counts of children and jobs count only theirs"
    ),
    "/api/v1/issues/{issue_id}": (
        "reads another repository's issue, or one with none, as a missing one (404); lists only the children and "
        "the jobs of the repositories the key covers"
    ),
    "/api/v1/issues/{issue_id}/activity": "reads another repository's issue as a missing one (404)",
    "/api/v1/issues/{issue_id}/inputs": (
        "reads another repository's issue as a missing one (404); lists only the questions of jobs in the "
        "repositories the key covers"
    ),
    "/api/v1/jobs": "names no issue the key does not read (issue_id null); lists the key's jobs since #318",
    "/api/v1/jobs/{job_id}": "names no issue the key does not read (issue_id null); another's job is missing (#318)",
    "/api/v1/jobs/{job_id}/inputs": "leaves out a question about an issue the key does not read",
    "/api/v1/merge-approvals": "lists the approvals of the repositories the key covers, narrowed before the limit (#318)",
    "/api/v1/merge-approvals/{approval_id}": "reads another repository's approval as a missing one (404), since #318",
    "/api/v1/stats": (
        "lists the recent and the costliest issues of the repositories the key covers, each with only its jobs "
        "there, and counts only theirs (the totals)"
    ),
    # Earlier stages: what names an issue, or a job's merge approval, among the work offered and decided.
    "/api/v1/proposals": "lists the issues offered in the repositories the key covers, since #297",
    "/api/v1/decisions": "lists the issues offered in the repositories the key covers, since #297",
    "/api/v1/decisions/{decision_id:path}": "reads another repository's decision as a missing one, since #297",
    "/api/v1/asks": "lists the asks about the repositories the key covers, since #297",
    "/api/v1/asks/of": (
        "the jobs a person started that wait: those the key reads, each titled with no issue the key does not "
        "read, and only their questions about issues it reads"
    ),
    "/api/v1/world": "shows the work of the repositories the key covers, since #297",
    "/api/v1/world/quests/{job_id}": "reads another repository's job, and its merge approval, as missing (#297)",
}

#: GET routes that show an issue, or a merge approval, of another repository inside something else, narrowed with that
#: thing as a whole in a stage of their own, and which.
ELSEWHERE: dict[str, str] = {
    "/api/v1/audit": "an issue's activity among every audit row: the audit reads",
}

_SECONDS = 10.0


def uncovered_parameters(values: Mapping[str, Mapping[str, str]], *, missing: bool = False) -> dict[str, str]:
    """Each path parameter's value naming the uncovered repository's item (its issue, question, job and merge
    approval), or with ``missing`` the issue, question and approval of the same kinds that do not exist."""
    return {
        **parameters(values, JOB[UNCOVERED]),
        "issue_id": MISSING if missing else ISSUE[UNCOVERED],
        "input_id": MISSING_INPUT if missing else INPUT[UNCOVERED],
        "approval_id": MISSING_APPROVAL if missing else APPROVAL[UNCOVERED],
    }


def crossing_parameters(values: Mapping[str, Mapping[str, str]], issue: str = MIXED) -> dict[str, str]:
    """Each path parameter's value naming an item of the covered repository whose work crosses into the other: by
    default the issue whose child, job and question are the other's; ``CHILD_HERE`` for the issue whose parent is."""
    return {
        **parameters(values, JOB_HERE),
        "issue_id": issue,
        "input_id": INPUT[COVERED],
        "approval_id": APPROVAL[COVERED],
    }


def parameter_sets(values: Mapping[str, Mapping[str, str]]) -> list[dict[str, str]]:
    return [uncovered_parameters(values), crossing_parameters(values), crossing_parameters(values, CHILD_HERE)]


#: Queries a list is also read with, so the walk reaches what its default leaves out: the child issues.
QUERIES: dict[str, tuple[str, ...]] = {
    "/api/v1/issues": ("?parent_issue_id=any&limit=200",),
    "/api/v1/asks/of": ("?person=ada@example.com",),
}


#: Each uncovered item a read may be addressed by, by the placeholder its id is put back as, with the id of the missing
#: one of its kind. A read addressed by the uncovered job alone is the job inventory's (test_job_reads_inventory.py);
#: here it shows what it may not when it names an issue or an approval.
_ITEMS = {
    "<issue>": (ISSUE[UNCOVERED], MISSING),
    "<input>": (INPUT[UNCOVERED], MISSING_INPUT),
    "<approval>": (APPROVAL[UNCOVERED], MISSING_APPROVAL),
}


def names(missing: bool = False) -> dict[str, str]:
    return {pair[1 if missing else 0]: placeholder for placeholder, pair in _ITEMS.items()}


def addresses_uncovered(url: str) -> bool:
    return any(item in url for item, _missing in _ITEMS.values())


async def _read(app: FastAPI, url: str, token: str) -> httpx.Response:
    try:
        response = await asyncio.wait_for(call(app, "GET", url, token), _SECONDS)
    except TimeoutError:
        pytest.fail(f"GET {url} did not answer in {_SECONDS}s: it measures nothing; list it in NO_ANSWER_HERE with why")
    assert response.status_code != 429, f"GET {url} was rate limited: the walk would measure nothing past it"
    return response


def _shows(response: httpx.Response) -> bool:
    text = response.text + str(response.headers)
    return shows_uncovered_issue(text) or shows_uncovered_approval(text)


async def serving(app: FastAPI, values: Mapping[str, Mapping[str, str]], token: str) -> dict[str, tuple[str, int]]:
    """Each GET route whose answer to ``token`` shows an issue of the uncovered repository, its work or its merge
    approval, with the first URL that did and its status."""
    found: dict[str, tuple[str, int]] = {}
    for path in get_routes(app):
        urls = [address(path, named) for named in parameter_sets(values)]
        for url in [*urls, *(f"{path}{query}" for query in QUERIES.get(path, ()))]:
            response = await _read(app, url, token)
            if addresses_uncovered(url):
                missing = await _read(app, address(path, uncovered_parameters(values, missing=True)), token)
                shows = same_answer(response, names(), missing, names(missing=True)) is not None
            else:
                shows = _shows(response)
            if shows:
                found.setdefault(path, (url, response.status_code))
    return found


async def test_the_routes_that_serve_an_issue_show_a_key_only_the_issues_its_allowlist_covers(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, values):
        served = {r.path for r in app_routes(app) if r.methods and "GET" in r.methods}
        limited = await serving(app, values, keys.limited)
        everyone = await serving(app, values, keys.open)
    stale = sorted((set(ISSUE_ROUTES) | set(ELSEWHERE) | set(STREAMS) | set(NO_ANSWER_HERE) | set(QUERIES)) - served)
    assert stale == [], f"listed here but not served: {stale}"
    shown = {path: seen for path, seen in limited.items() if path not in ELSEWHERE}
    assert shown == {}, (
        f"routes that showed a key covering only {COVERED} an issue of {UNCOVERED}, its work or its merge approval: "
        f"{shown}. A route that reads one issue answers another repository's as a missing one "
        "(embry0.api.issue_allowlist), and a list narrows its query to the repositories the key covers"
    )
    unplaced = sorted(set(everyone) - set(ISSUE_ROUTES) - set(ELSEWHERE))
    assert unplaced == [], (
        f"routes that serve an issue or a merge approval and are not listed: {unplaced}. Apply the key's allowlist "
        "and list each in ISSUE_ROUTES with how, or place it in ELSEWHERE with the stage that narrows it"
    )
    quiet = sorted((set(ISSUE_ROUTES) | set(ELSEWHERE)) - set(everyone))
    assert quiet == [], (
        f"listed routes that did not show the issue or the approval to a key without an allowlist: {quiet}: the "
        "inventory no longer measures them; seed what they read in tests/fixtures/issue_reads.py, or take them out"
    )


#: The GET routes that read one issue or one merge approval.
ONE_ITEM = [path for path in ISSUE_ROUTES if "{issue_id}" in path or "{approval_id}" in path]


@pytest.mark.parametrize("path", ONE_ITEM)
async def test_another_repository_s_issue_or_approval_reads_as_one_that_does_not_exist(
    path: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, values):
        uncovered_url = address(path, uncovered_parameters(values))
        missing_url = address(path, uncovered_parameters(values, missing=True))
        own_url = address(
            path, {**uncovered_parameters(values), "issue_id": ISSUE[COVERED], "approval_id": APPROVAL[COVERED]}
        )
        uncovered = await call(app, "GET", uncovered_url, keys.limited)
        missing = await call(app, "GET", missing_url, keys.limited)
        missing_to_everyone = await call(app, "GET", missing_url, keys.open)
        reached = await call(app, "GET", uncovered_url, keys.open)
        own_read = await call(app, "GET", own_url, keys.limited)
    differ = same_answer(uncovered, names(), missing, names(missing=True))
    assert differ is None, f"{path}: the limited key reads {UNCOVERED}'s item as a missing one: {differ}"
    differ = same_answer(missing, names(missing=True), missing_to_everyone, names(missing=True))
    assert differ is None, f"{path}: the limited key's answer for a missing item is everyone's: {differ}"
    assert reached.status_code == 200 and _shows(reached), (
        f"{path}: a key without an allowlist reads the item, so the check measures something: "
        f"{reached.status_code} {reached.text[:200]}"
    )
    assert own_read.status_code == 200 and same_answer(own_read, {}, missing, names(missing=True)) is not None, (
        f"{path}: the limited key reads its own repository's item: {own_read.status_code} {own_read.text[:200]}"
    )


async def test_the_inventory_finds_a_new_route_that_serves_an_issue_without_the_rule(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not quiet by construction: a route serving an issue's row as it is stored, and one listing every issue, are
    found and reported."""
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, values):
        extra = APIRouter()

        @extra.get("/issues/{issue_id}/raw-for-a-test")
        async def raw(issue_id: str, request: Request) -> dict[str, Any]:
            return dict(await request.app.state.issues_repo.get(issue_id) or {})

        @extra.get("/every-issue-for-a-test")
        async def every(request: Request) -> list[dict[str, Any]]:
            rows, _total = await request.app.state.issues_repo.list_all(limit=200)
            return [dict(row) for row in rows]

        app.include_router(extra, prefix="/api/v1")
        found = await serving(app, values, keys.limited)
    assert {path for path in found if path not in ELSEWHERE} == {
        "/api/v1/issues/{issue_id}/raw-for-a-test",
        "/api/v1/every-issue-for-a-test",
    }, f"the walk reports both new routes and no route in ISSUE_ROUTES: {found}"
