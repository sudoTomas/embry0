"""Organization actions against the real repositories they reuse (ADR 0010).

The service tests use fakes; this pins that the real registry, watcher
stacks, QA configs and jobs tables answer the calls the actions make, so a
fake cannot drift from the class it stands in for.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import pytest

from embry0.services.org_actions import ActionContext, run_action
from embry0.services.watcher_stacks import WatcherStack
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import run_migrations
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.repo_qa_configs import RepoQAConfigsRepository
from embry0.storage.repositories.repos import ReposRepository
from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

pytestmark = pytest.mark.requires_postgres

REPOS = ("acme/actions-widgets", "Acme/Actions-Gadgets", "acme/actions-gadgets")


async def _clean(db: DatabasePool) -> None:
    for table in ("jobs", "watcher_stacks", "repo_qa_configs", "repos"):
        await db.execute(f"DELETE FROM {table} WHERE repo = ANY($1::text[])", list(REPOS))  # noqa: S608


@pytest.fixture
async def db() -> AsyncIterator[DatabasePool]:
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    pool = DatabasePool(url)
    await pool.connect()
    await run_migrations(pool)
    await _clean(pool)
    yield pool
    await _clean(pool)
    await pool.close()


class _Starter:
    """The API's onboard path, which no table stands in for: it would accept every repository."""

    def refusal(self, repo: str) -> str | None:
        return None

    async def start_onboard_job(self, repo: str, branch: str) -> str:
        raise AssertionError("a dry run starts nothing")


def _context(db: DatabasePool) -> ActionContext:
    def fact(repo: str) -> dict[str, Any]:
        return {"repo": repo, "archived": False, "default_branch": "main", "facts": {"boot_hint": "npm run dev"}}

    return ActionContext(
        owner="acme",
        forge="github",
        facts={
            "acme/actions-widgets": fact("acme/actions-widgets"),
            "acme/actions-gadgets": fact("Acme/Actions-Gadgets"),
        },
        registry=ReposRepository(db, default_forge=""),
        qa_configs=RepoQAConfigsRepository(db, default_forge=""),
        stacks=WatcherStacksRepository(db),
        jobs=JobsRepository(db),
        job_starter=_Starter(),
    )


@pytest.mark.asyncio
async def test_register_and_watch_are_idempotent_on_the_real_tables(db: DatabasePool) -> None:
    ctx = _context(db)
    await db.execute("INSERT INTO repos (repo, notes, forge) VALUES ('acme/actions-gadgets', '', 'github')")
    asked = ["acme/actions-widgets", "acme/actions-gadgets"]

    first = await run_action(ctx, "register", asked)
    second = await run_action(ctx, "register", asked)
    assert [(r.repo, r.outcome) for r in first] == [
        ("acme/actions-widgets", "done"),
        ("Acme/Actions-Gadgets", "already"),
    ]
    assert first[1].detail == "registered as acme/actions-gadgets"
    assert [r.outcome for r in second] == ["already", "already"]
    rows = await db.fetch("SELECT repo, forge FROM repos WHERE repo = ANY($1::text[]) ORDER BY repo", list(REPOS))
    assert [(r["repo"], r["forge"]) for r in rows] == [
        ("acme/actions-gadgets", "github"),
        ("acme/actions-widgets", "github"),
    ]

    watched = await run_action(ctx, "watch", ["acme/actions-widgets"])
    again = await run_action(ctx, "watch", ["acme/actions-widgets"])
    assert [r.outcome for r in watched] == ["done"] and [r.outcome for r in again] == ["already"]
    stack = await WatcherStacksRepository(db).get("acme/actions-widgets")
    assert stack is not None and stack.enabled is False and stack.tracker == "github"


@pytest.mark.asyncio
async def test_onboard_dry_run_reads_the_real_qa_and_jobs_tables(db: DatabasePool) -> None:
    ctx = _context(db)
    await run_action(ctx, "register", ["acme/actions-widgets", "acme/actions-gadgets"])
    await RepoQAConfigsRepository(db, default_forge="").upsert("acme/actions-widgets", "version: 2\n")
    results = await run_action(ctx, "onboard", ["acme/actions-widgets", "acme/actions-gadgets"], dry_run=True)
    assert [(r.repo, r.outcome) for r in results] == [
        ("acme/actions-widgets", "already"),
        ("Acme/Actions-Gadgets", "done"),
    ]
    assert results[1].detail == "would start an onboard job on main"


@pytest.mark.asyncio
async def test_watch_matches_a_stack_stored_under_another_case(db: DatabasePool) -> None:
    stacks = WatcherStacksRepository(db)
    await stacks.upsert(WatcherStack(repo="acme/actions-gadgets", logql='{app="gadgets"}', tracker="github"))
    results = await run_action(_context(db), "watch", ["acme/actions-gadgets"])
    assert [(r.repo, r.outcome) for r in results] == [("Acme/Actions-Gadgets", "already")]
    assert results[0].detail == "a watcher stack exists as acme/actions-gadgets"
    rows = await db.fetch("SELECT repo FROM watcher_stacks WHERE lower(repo) = 'acme/actions-gadgets'")
    assert [r["repo"] for r in rows] == ["acme/actions-gadgets"]


@pytest.mark.asyncio
async def test_a_completed_onboard_job_holds_a_rerun_behind_many_newer_jobs(db: DatabasePool) -> None:
    """More than a page of newer jobs of other pipelines must not hide the last onboard job."""
    ctx = _context(db)
    await run_action(ctx, "register", ["acme/actions-widgets"])
    jobs = JobsRepository(db)
    onboard = await jobs.create(repo="acme/actions-widgets", task="onboard", pipeline_template="onboard")
    await db.execute(
        "UPDATE jobs SET status = 'completed', created_at = now() - interval '1 day' WHERE job_id = $1", onboard
    )
    for n in range(55):
        await jobs.create(repo="acme/actions-widgets", task=f"issue {n}", pipeline_template="issue-to-pr")
    results = await run_action(ctx, "onboard", ["acme/actions-widgets"])
    assert [(r.repo, r.outcome) for r in results] == [("acme/actions-widgets", "skipped")]
    assert onboard in results[0].detail
