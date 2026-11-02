"""Every GET route that serves a job reads, to a key with a repository allowlist, only the jobs it covers.

On the real app over a real database (:mod:`tests.fixtures.job_reads`), with
a finished job in each of two repositories and something of every kind
stored for each, every GET route the app serves under ``/api/v1`` is read
through an admin key whose list covers one repository and through an admin
key without a list. A path parameter that names a job, a QA run or a
repository names the uncovered one; the others name its items as
:func:`tests.fixtures.repository_writes.ids` does.

- No route in :data:`JOB_ROUTES` shows the limited key the uncovered job. A
  route addressed by the job shows it when its answer is not the one it
  gives for a job that does not exist; any other, when its answer names the
  job or anything stored for it.
- The routes that show it to the key without a list are exactly
  :data:`JOB_ROUTES` and :data:`ELSEWHERE`. A new route that serves a job
  fails until it applies the rule and is listed with how, or is placed in
  :data:`ELSEWHERE` with the work that narrows it; a listed route that no
  longer serves the job fails until it is taken out.
- Each route in :data:`JOB_ROUTES` that reads one job or run answers the
  limited key on the uncovered job byte for byte as on a job that does not
  exist (status, headers and body, each id put back as ``<job>``), that
  answer is the one every caller gets for a job that does not exist, and the
  limited key still reads its own job.

The streams are :data:`STREAMS`: they do not end, so they are read in
``test_job_streams_allowlist.py``. :data:`NO_ANSWER_HERE` lists the routes
that cannot answer in this app; any other route that does not answer fails
the walk, so none is passed over unread.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Request

from embry0.storage.database import DatabasePool
from tests.fixtures.job_reads import (
    APP,
    JOB,
    MISSING,
    deliverable_id,
    job_reads_app,
    same_answer,
    shows_uncovered_job,
)
from tests.fixtures.repository_writes import COVERED, UNCOVERED, call
from tests.fixtures.routes import app_routes

pytestmark = pytest.mark.requires_postgres

#: Every GET route that serves a job, a job's part or a list of jobs, and how it applies a key's allowlist.
JOB_ROUTES: dict[str, str] = {
    "/api/v1/jobs": "lists the jobs of the repositories the key covers, narrowed before the page and the total",
    "/api/v1/jobs/{job_id}": "reads another repository's job as a missing one (404)",
    "/api/v1/jobs/{job_id}/inputs": "has no questions for another repository's job, as for a missing one ([])",
    "/api/v1/jobs/{job_id}/logs/events": "has no events for another repository's job, as for a missing one",
    "/api/v1/jobs/{job_id}/verification": "has no verification for another repository's job (404)",
    "/api/v1/jobs/{job_id}/agents/{agent_type}/session": "has no stored session for another repository's job (404)",
    "/api/v1/jobs/{job_id}/deliverables": "has no deliverables for another repository's job ([])",
    "/api/v1/jobs/{job_id}/deliverables/{deliverable_id}/download": "finds no deliverable of another's job (404)",
    "/api/v1/jobs/{job_id}/artifacts/screenshots/latest": "finds no screenshot of another repository's job (404)",
    "/api/v1/jobs/{job_id}/artifacts/logs/{service}": "finds no attempt of another repository's job (404)",
    "/api/v1/jobs/{job_id}/artifacts/{path:path}": "finds no attempt of another repository's job (404)",
    "/api/v1/jobs/{job_id}/qa/services": "has no live workspace for another repository's job ([])",
    "/api/v1/jobs/{job_id}/qa/attempts": "has no QA attempts for another repository's job ([])",
    "/api/v1/jobs/{job_id}/qa/attempts/{attempt_n}/result": "finds no result of another repository's job (404)",
    "/api/v1/jobs/{job_id}/qa/screenshots/latest": "finds no screenshot of another repository's job (404)",
    "/api/v1/jobs/{job_id}/qa/attempts/{attempt_n}/artifacts/{path:path}": "finds no artifact of another's job (404)",
    "/api/v1/qa/repos": "lists the QA runs of the repositories the key covers, narrowed before the limit",
    "/api/v1/qa/repos/{repo:path}/runs": "lists the runs of the repositories the key covers: none of another's",
    "/api/v1/qa/repos/{repo:path}/apps/{app}/history": "lists the runs of the repositories the key covers",
    "/api/v1/qa/runs/{run_id}": "reads another repository's QA run as a missing one (404)",
    "/api/v1/qa/runs/{run_id}/apps/{app}": "reads another repository's QA run as a missing one (404)",
    "/api/v1/qa/runs/{run_id}/affected_set": "reads another repository's QA run as a missing one (404)",
    "/api/v1/qa/runs/{run_id}/apps/{app}/artifacts/{kind}": "has no artifacts of another repository's run ([])",
    "/api/v1/qa/runs/{run_id}/apps/{app}/artifacts/{kind}/{filename}": "finds no artifact of another's run (404)",
    "/api/v1/sandboxes/active": "lists the sandboxes of jobs in the repositories the key covers",
    "/api/v1/world/quests/{job_id}": "reads another repository's job as a missing one (404), since #297",
    "/api/v1/world": "shows the parties, inbox and loot of the repositories the key covers, since #297",
    "/api/v1/world/characters/{agent_type}": "lists a character's quests in the repositories the key covers (#297)",
    "/api/v1/decisions": "lists the quests to review in the repositories the key covers, since #297",
    "/api/v1/decisions/{decision_id:path}": "reads a finished job of another repository as a missing one (#297)",
}

#: GET routes that name a job of another repository inside something else they read, and the read boundary that
#: narrows that thing as a whole, in its own change.
ELSEWHERE: dict[str, str] = {
    "/api/v1/issues/{issue_id}": "an issue, with its jobs: the issue reads",
    "/api/v1/issues/{issue_id}/inputs": "an issue's questions, with their jobs: the issue reads",
    "/api/v1/cases/{case_id}": "a case, with its jobs and pull requests: the case reads",
    "/api/v1/traces": "the agents' runs, by job: the trace reads",
    "/api/v1/merge-approvals": "the merge approvals, each naming its job: the merge approval reads",
    "/api/v1/merge-approvals/{approval_id}": "a merge approval, naming its job: the merge approval reads",
    "/api/v1/repos/{full_repo:repo}": "a repository, with its standards' last audit job: the repository reads",
    "/api/v1/repos/{full_repo:repo}/-": "the same handler",
    "/api/v1/repos/{full_repo:repo}/-/": "the same handler",
    "/api/v1/repos/{full_repo:repo}/-/readiness": "a repository's checklist, with its onboard job: the repository reads",
    "/api/v1/repos/{full_repo:repo}/readiness": "the old path of the checklist",
    "/api/v1/repos/{full_repo:repo}/-/setup/proposal": "a repository's proposed test setup, with its job: the "
    "repository reads",
    "/api/v1/repos/{full_repo:repo}/setup/proposal": "the old path of the proposal",
    "/api/v1/standards/{owner}": "an owner's standard, with each repository's last audit job: the organization reads",
    "/api/v1/standards/{owner}/{kind}/repos/{repo:path}": "a repository's place under a standard, with its last "
    "audit job: the repository reads",
    "/api/v1/maps/{slug}/work": "a knowledge map's work, each part with its job: the knowledge maps, read by owner",
}

#: GET routes that stream until the client leaves: read in test_job_streams_allowlist.py.
STREAMS: dict[str, str] = {
    "/api/v1/qa/runs/{run_id}/events": "a QA run's events (server-sent events)",
}

#: GET routes that cannot answer in this app, and why; any other that does not answer fails the walk.
NO_ANSWER_HERE: dict[str, str] = {
    path: "reads a repository's environment template from the forge, whose client retries while no network answers"
    for path in (
        "/api/v1/repos/{full_repo:repo}/-/environment/detect",
        "/api/v1/repos/{full_repo:repo}/environment/detect",
    )
}

_PARAMETER = re.compile(r"\{(\w+)(?::\w+)?\}")
#: Long enough for any route here to answer.
_SECONDS = 10.0


def parameters(values: Mapping[str, Mapping[str, str]], job: str = JOB[UNCOVERED]) -> dict[str, str]:
    """Each path parameter's value: ``job`` for a job or a QA run, the uncovered repository's items otherwise."""
    return {
        **values[UNCOVERED],
        "job_id": job,
        "run_id": job,
        "decision_id": f"done:{job}",
        "agent_type": "developer",
        "deliverable_id": deliverable_id(UNCOVERED),
        "attempt_n": "1",
        "path": "result.json",
        "service": APP,
        "app": APP,
        "filename": "1-home.png",
    }


def address(path: str, values: Mapping[str, str]) -> str:
    """``path`` naming ``values`` (``x`` for what names nothing here); a QA run's artifacts are of a kind."""
    named = dict(values, kind="screenshots") if path.startswith("/api/v1/qa/runs/") else values
    return _PARAMETER.sub(lambda m: named.get(m.group(1), "x"), path)


def get_routes(app: FastAPI) -> list[str]:
    return sorted(
        {
            r.path
            for r in app_routes(app)
            if r.is_api_route and r.path.startswith("/api/v1/") and r.methods and "GET" in r.methods
        }
        - set(STREAMS)
        - set(NO_ANSWER_HERE)
    )


async def _read(app: FastAPI, url: str, token: str) -> httpx.Response:
    try:
        return await asyncio.wait_for(call(app, "GET", url, token), _SECONDS)
    except TimeoutError:
        pytest.fail(f"GET {url} did not answer in {_SECONDS}s: it measures nothing; list it in NO_ANSWER_HERE with why")


def _shows(response: httpx.Response) -> bool:
    return shows_uncovered_job(response.text) or shows_uncovered_job(str(response.headers))


async def serving(app: FastAPI, values: Mapping[str, Mapping[str, str]], token: str) -> dict[str, int]:
    """Each GET route whose answer to ``token`` shows the uncovered repository's job, with its status.

    A route addressed by the job shows it when its answer is not the one it gives for a job that does not exist,
    whose id it may repeat; any other route, when its answer names the job or anything stored for it."""
    named = parameters(values)
    found: dict[str, int] = {}
    for path in get_routes(app):
        url = address(path, named)
        response = await _read(app, url, token)
        if JOB[UNCOVERED] in url:
            missing = await _read(app, address(path, parameters(values, MISSING)), token)
            shows = same_answer(response, JOB[UNCOVERED], missing, MISSING) is not None
        else:
            shows = _shows(response)
        if shows:
            found[path] = response.status_code
    return found


async def test_the_routes_that_serve_a_job_show_a_key_only_the_jobs_its_allowlist_covers(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with job_reads_app(db_with_migrations, monkeypatch) as (app, keys, values):
        served = {r.path for r in app_routes(app) if r.methods and "GET" in r.methods}
        limited = await serving(app, values, keys.limited)
        everyone = await serving(app, values, keys.open)
    stale = sorted((set(STREAMS) | set(NO_ANSWER_HERE)) - served)
    assert stale == [], f"listed here but not served: {stale}"
    shown = {path: status for path, status in limited.items() if path not in ELSEWHERE}
    assert shown == {}, (
        f"routes that showed a key covering only {COVERED} the job of {UNCOVERED}: {shown}. A route that reads one job "
        "answers another repository's as a missing one (embry0.api.job_allowlist), and a list narrows its query to "
        "the repositories the key covers (embry0.api.repo_allowlist.covered_slugs)"
    )
    unplaced = sorted(set(everyone) - set(JOB_ROUTES) - set(ELSEWHERE))
    assert unplaced == [], (
        f"routes that serve a job and are not listed: {unplaced}. Apply the key's allowlist and list each in "
        "JOB_ROUTES with how, or place it in ELSEWHERE with the work that narrows it"
    )
    quiet = sorted((set(JOB_ROUTES) | set(ELSEWHERE)) - set(everyone))
    assert quiet == [], (
        f"listed routes that did not show the job to a key without an allowlist: {quiet}: the inventory no longer "
        "measures them; seed what they read in tests/fixtures/job_reads.py, or take them out"
    )


#: The routes in JOB_ROUTES that read one job or QA run; #297's own, the quest scroll and a decision, are held to
#: the same rule by its tests.
ONE_JOB = [
    path
    for path in JOB_ROUTES
    if ("{job_id}" in path or "{run_id}" in path) and path != "/api/v1/world/quests/{job_id}"
]


@pytest.mark.parametrize("path", ONE_JOB)
async def test_another_repository_s_job_reads_as_a_job_that_does_not_exist(
    path: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with job_reads_app(db_with_migrations, monkeypatch) as (app, keys, values):
        uncovered = await call(app, "GET", address(path, parameters(values)), keys.limited)
        missing = await call(app, "GET", address(path, parameters(values, MISSING)), keys.limited)
        missing_to_everyone = await call(app, "GET", address(path, parameters(values, MISSING)), keys.open)
        reached = await call(app, "GET", address(path, parameters(values)), keys.open)
        own = {**parameters(values, JOB[COVERED]), "deliverable_id": deliverable_id(COVERED)}
        own_read = await call(app, "GET", address(path, own), keys.limited)
    differ = same_answer(uncovered, JOB[UNCOVERED], missing, MISSING)
    assert differ is None, f"{path}: the limited key reads {UNCOVERED}'s job as a missing one: {differ}"
    differ = same_answer(missing, MISSING, missing_to_everyone, MISSING)
    assert differ is None, f"{path}: the limited key's answer for a missing job is everyone's: {differ}"
    assert same_answer(reached, JOB[UNCOVERED], missing, MISSING) is not None, (
        f"{path}: a key without an allowlist reads the job, so the check measures something: "
        f"{reached.status_code} {reached.text[:200]}"
    )
    assert same_answer(own_read, JOB[COVERED], missing, MISSING) is not None, (
        f"{path}: the limited key reads its own repository's job: {own_read.status_code} {own_read.text[:200]}"
    )


async def test_the_inventory_finds_a_new_route_that_serves_a_job_without_the_rule(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not quiet by construction: a route serving a job's row as it is stored is found and reported."""
    async with job_reads_app(db_with_migrations, monkeypatch) as (app, keys, values):
        extra = APIRouter()

        @extra.get("/jobs/{job_id}/raw-for-a-test")
        async def raw(job_id: str, request: Request) -> dict[str, Any]:
            return dict(await request.app.state.jobs_repo.get(job_id) or {})

        app.include_router(extra, prefix="/api/v1")
        found = await serving(app, values, keys.limited)
    assert {path: status for path, status in found.items() if path not in ELSEWHERE} == {
        "/api/v1/jobs/{job_id}/raw-for-a-test": 200
    }, f"the walk reports the new route serving another repository's job, and no route in JOB_ROUTES: {found}"
