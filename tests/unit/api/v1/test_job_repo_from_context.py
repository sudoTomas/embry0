"""A job posted with a git context alone is stored under the repository it works on.

``POST /jobs`` takes the repository as ``repo`` or as a git ``context``. On
the real app over a real database (:mod:`tests.fixtures.repository_writes`),
a job posted either way is stored with that repository in ``jobs.repo``, so
``GET /jobs?repo=`` lists it and ``GET /stats`` counts it under it; a job
over a URL names none.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, MASTER, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

#: A repository no seeded job works on, so its list and its stats hold only the jobs posted here.
REPO = f"{COVERED.split('/')[0]}/context-only"


async def test_a_job_posted_with_a_git_context_alone_is_stored_listed_and_counted_under_its_repository(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, _keys, _values):
        monkeypatch.setattr(app.state.issue_executor, "submit_job", MagicMock())
        posted = {
            "by context": await call(
                app, "POST", "/api/v1/jobs", MASTER, {"task": "Fix it", "context": {"type": "git", "repo": REPO}}
            ),
            "by repo": await call(app, "POST", "/api/v1/jobs", MASTER, {"task": "Fix it too", "repo": REPO}),
            "over a url": await call(
                app,
                "POST",
                "/api/v1/jobs",
                MASTER,
                {"task": "Read it", "context": {"type": "http", "url": "https://example.com/report"}},
            ),
        }
        made = {name: response.json().get("job_id") for name, response in posted.items()}
        stored = {name: await db.fetchval("SELECT repo FROM jobs WHERE job_id = $1", job) for name, job in made.items()}
        await db.execute("UPDATE jobs SET total_cost_usd = 1.5 WHERE job_id = $1", made["by context"])
        listed = (await call(app, "GET", f"/api/v1/jobs?repo={REPO}", MASTER)).json()
        stats = (await call(app, "GET", "/api/v1/stats", MASTER)).json()
        audited = await db.fetchval(
            "SELECT details->>'repo' FROM audit_log WHERE action = 'job_created' AND details->>'job_id' = $1",
            made["by context"],
        )
    assert all(r.status_code == 201 for r in posted.values()), {
        n: (r.status_code, r.text[:200]) for n, r in posted.items()
    }
    assert stored == {"by context": REPO, "by repo": REPO, "over a url": None}, (
        f"each job is stored under the repository it works on, a git context's included: {stored}"
    )
    assert sorted(j["job_id"] for j in listed["jobs"]) == sorted([made["by context"], made["by repo"]]), (
        f"GET /jobs?repo= lists the job posted with a context: {listed}"
    )
    assert {r["repo"]: r["job_count"] for r in stats["cost_by_repo"]}.get(REPO) == 2, (
        f"GET /stats counts the job posted with a context under its repository: {stats['cost_by_repo']}"
    )
    assert audited == REPO, f"its job_created audit row names the repository: {audited!r}"
