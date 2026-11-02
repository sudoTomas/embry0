"""The lists and counts of jobs a key with a repository allowlist reads hold only the jobs of its repositories.

On the real app over a real database (:mod:`tests.fixtures.job_reads`). A
list narrows its query to the key's repositories before it pages, limits and
counts, so every page holds the key's own jobs however many newer jobs other
repositories have, and its total counts them alone. A job belongs to the
repository it works on: its ``repo``, else its git context's; a job with no
repository is no restricted key's.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.job_reads import APP, JOB, audit_job, job_reads_app
from tests.fixtures.repository_writes import COVERED, NOW, UNCOVERED, call

pytestmark = pytest.mark.requires_postgres

#: How many newer jobs the repository the key does not cover has: more than a page holds.
NEWER = 70
#: A job posted with a git context alone, in the covered repository: its row stores no ``repo``.
CONTEXT_ONLY = "job-context-widget"
#: The same in the uncovered repository.
CONTEXT_ONLY_ELSEWHERE = "job-context-gizmo"
#: A job with no repository: over a URL.
NO_REPOSITORY = "job-url-only"


async def _more_jobs(db: DatabasePool) -> None:
    for i in range(NEWER):
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at) "
            "VALUES ($1, $2, 'Newer', $3, 'full-magnum-opus', $4, $4)",
            f"job-newer-{i:03d}",
            UNCOVERED,
            ("running", "pending", "paused", "awaiting_input")[i % 4],
            NOW - timedelta(seconds=60 - i * 0.5),
        )
    for job, context, status in (
        (CONTEXT_ONLY, {"type": "git", "repo": COVERED}, "running"),
        (CONTEXT_ONLY_ELSEWHERE, {"type": "git", "repo": UNCOVERED}, "running"),
        (NO_REPOSITORY, {"type": "http", "url": "https://example.com/report"}, "pending"),
    ):
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, context, created_at) VALUES ($1, NULL, 'Task', $2, $3, $4)",
            job,
            status,
            context,
            NOW - timedelta(minutes=30),
        )


#: The jobs the limited key reads: its repository's, by ``repo`` or by context.
OWN = {JOB[COVERED], audit_job(COVERED), CONTEXT_ONLY}


async def _pages(app: Any, token: str, query: str = "") -> tuple[list[str], set[int]]:
    """Every job id ``GET /jobs`` lists to ``token``, one per page, and the totals the pages report."""
    seen: list[str] = []
    totals: set[int] = set()
    offset = 0
    while True:
        body = (await call(app, "GET", f"/api/v1/jobs?limit=1&offset={offset}{query}", token)).json()
        totals.add(body["total"])
        if not body["jobs"]:
            return seen, totals
        seen += [j["job_id"] for j in body["jobs"]]
        offset += 1


async def test_the_jobs_list_pages_and_counts_only_the_key_s_jobs(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with job_reads_app(db, monkeypatch) as (app, keys, _values):
        await _more_jobs(db)
        limited, limited_totals = await _pages(app, keys.limited)
        first = (await call(app, "GET", "/api/v1/jobs?limit=50", keys.open)).json()
        everyone_total = first["total"]
        other = (await call(app, "GET", f"/api/v1/jobs?repo={UNCOVERED}", keys.limited)).json()
        nobody = (await call(app, "GET", "/api/v1/jobs?repo=nobody/none", keys.limited)).json()
        running, _ = await _pages(app, keys.limited, "&status=running")
    assert all(j["repo"] == UNCOVERED for j in first["jobs"][: NEWER // 2]), "the newer jobs fill the first page"
    assert sorted(limited) == sorted(OWN), f"the limited key's pages hold its own jobs, each once: {limited}"
    assert limited_totals == {len(OWN)}, f"every page's total counts the key's jobs alone: {limited_totals}"
    # The four seeded jobs (a finished one and a context audit in each repository), the newer ones, and three.
    assert everyone_total == 4 + NEWER + 3, f"a key without a list counts every job: {everyone_total}"
    assert other == nobody and other["total"] == 0, (
        f"another repository's jobs list as a repository with none: {other} against {nobody}"
    )
    assert running == [CONTEXT_ONLY], f"a status filter is narrowed too: {running}"


async def test_a_job_s_repository_is_its_context_s_when_its_row_names_none(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job posted with a git context alone stores no ``repo``: it is read, and acted on, as its context's
    repository's. One with no repository is no restricted key's."""
    db = db_with_migrations
    jobs = (CONTEXT_ONLY, CONTEXT_ONLY_ELSEWHERE, NO_REPOSITORY)
    done_when = {"items": [{"done": "it works"}]}
    async with job_reads_app(db, monkeypatch) as (app, keys, _values):
        await _more_jobs(db)
        shown = {}
        for job in jobs:
            shown[job] = (
                (await call(app, "GET", f"/api/v1/jobs/{job}", keys.limited)).status_code,
                (await call(app, "GET", f"/api/v1/world/quests/{job}", keys.limited)).status_code,
                (await call(app, "PUT", f"/api/v1/jobs/{job}/done-when", keys.limited, done_when)).status_code,
            )
        everyone = {job: (await call(app, "GET", f"/api/v1/jobs/{job}", keys.open)).status_code for job in jobs}
    assert shown == {CONTEXT_ONLY: (200, 200, 200), CONTEXT_ONLY_ELSEWHERE: (404,) * 3, NO_REPOSITORY: (404,) * 3}, (
        f"the limited key reads and acts on the job whose context is its repository's, and on no other: {shown}"
    )
    assert set(everyone.values()) == {200}, f"a key without a list reads each: {everyone}"


async def test_the_queue_counts_only_the_key_s_active_jobs(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with job_reads_app(db, monkeypatch) as (app, keys, _values):
        await _more_jobs(db)
        limited = (await call(app, "GET", "/api/v1/queue", keys.limited)).json()
        everyone = (await call(app, "GET", "/api/v1/queue", keys.open)).json()
    assert limited == {"depth": 0, "pending": 0, "running": 1, "awaiting_input": 0, "paused": 0}, (
        f"the limited key counts its one running job: {limited}"
    )
    assert everyone == {"depth": 19, "pending": 19, "running": 20, "awaiting_input": 17, "paused": 17}, everyone


async def _newer_qa_run(db: DatabasePool) -> None:
    """A QA run in the uncovered repository newer than every other, for the lists that limit."""
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, finished_at) "
        "VALUES ('job-qa-newest', $1, 'QA', 'completed', 'qa', $2, $2, $2)",
        UNCOVERED,
        NOW - timedelta(minutes=1),
    )
    await db.execute(
        "INSERT INTO qa_app_results (job_id, app_name, status, duration_ms) VALUES ('job-qa-newest', $1, 'passed', 1)",
        APP,
    )


async def test_the_qa_lists_hold_only_the_key_s_runs(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with job_reads_app(db, monkeypatch) as (app, keys, _values):
        await _newer_qa_run(db)
        repos = (await call(app, "GET", "/api/v1/qa/repos?limit=1", keys.limited)).json()
        everyone = (await call(app, "GET", "/api/v1/qa/repos?limit=1", keys.open)).json()
        lists = {
            path: [
                (await call(app, "GET", f"/api/v1/qa/repos/{repo}/{path}", keys.limited)).json()
                for repo in (UNCOVERED, "nobody/none", COVERED)
            ]
            for path in ("runs", f"apps/{APP}/history")
        }
    assert [r["repo"] for r in everyone] == [UNCOVERED], "the uncovered repository's run is the newest"
    assert [(r["repo"], r["latest_run_id"]) for r in repos] == [(COVERED, JOB[COVERED])], (
        f"the limited key's page holds its own repository's run: {repos}"
    )
    for path, (other, nobody, own) in lists.items():
        assert other == nobody == [], f"{path}: another repository's runs list as a repository with none: {other}"
        assert [r["job_id"] for r in own] == [JOB[COVERED]], f"{path}: the key's own runs are listed: {own}"


@pytest.mark.parametrize("path", ["cache/analytics", "flake"])
async def test_a_qa_aggregate_of_another_repository_counts_no_run(
    path: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with job_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        other = await call(app, "GET", f"/api/v1/qa/repos/{UNCOVERED}/{path}", keys.limited)
        nobody = await call(app, "GET", "/api/v1/qa/repos/nobody/nobody/" + path, keys.limited)
        everyone = await call(app, "GET", f"/api/v1/qa/repos/{UNCOVERED}/{path}", keys.open)
        own = await call(app, "GET", f"/api/v1/qa/repos/{COVERED}/{path}", keys.limited)

    def shown(response: Any, repo: str) -> Any:
        return response.status_code, response.text.replace(repo, "<repo>")

    assert len("nobody/nobody") == len(UNCOVERED), "the two answers compare byte for byte"
    assert shown(other, UNCOVERED) == shown(nobody, "nobody/nobody"), (
        f"{path}: another repository's runs count as a repository with none: {other.text} against {nobody.text}"
    )
    assert shown(everyone, UNCOVERED) != shown(nobody, "nobody/nobody"), f"{path}: it counts runs: {everyone.text}"
    assert shown(own, COVERED) == shown(everyone, UNCOVERED), f"{path}: the key counts its own runs: {own.text}"


async def test_the_sandboxes_listed_are_those_of_the_key_s_jobs(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with job_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        limited = (await call(app, "GET", "/api/v1/sandboxes/active", keys.limited)).json()
        everyone = (await call(app, "GET", "/api/v1/sandboxes/active", keys.open)).json()
    own = {f"sandbox-{JOB[COVERED]}", f"sandbox-{JOB[COVERED]}__{APP}"}
    assert {c["name"] for c in limited["containers"]} == own and limited["count"] == 2, (
        f"the limited key is shown its own jobs' sandboxes and no other: {limited}"
    )
    assert everyone["count"] == 4, f"a key without a list is shown every job's: {everyone}"
