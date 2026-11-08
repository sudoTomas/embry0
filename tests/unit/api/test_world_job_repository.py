"""The world and the asks feed read a job's repository as the jobs routes do: its ``repo``, else its git context's.

``POST /jobs`` stores the repository a git context names, and migration 141
filled the rows written before it; a row that still names none, and whose
context does, is read as that repository's by the jobs routes
(``jobs.job_repository_sql``). The world's parties, inbox, loot, quest logs,
regions and slop cleanups, one quest, and the quests to review built from the
inbox read it the same way, and so does the asks feed for the asks about
such a job's decision: a key with a repository allowlist sees such a job when
its list covers the context's repository, and only then. A row that names a
repository keeps it, spelled as stored. The rows here are written straight to
the table.

On the real app over a real database (:mod:`tests.fixtures.repository_writes`).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from embry0.services.slop import SLOPMASTER
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.asks import AsksRepository
from embry0.storage.repositories.jobs import job_repository_sql
from embry0.storage.repositories.world import WorldRepository
from tests.fixtures.repository_writes import COVERED, NOW, UNCOVERED, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres


def _done(repo: str) -> str:
    return f"job-context-done-{repo.split('/')[1]}"


def _live(repo: str) -> str:
    return f"job-context-live-{repo.split('/')[1]}"


async def _context_only_jobs(db: DatabasePool) -> None:
    """In each repository, a finished job with a pull request the developer ran and a running job, both posted with
    a git context and no ``repo``."""
    for repo in (COVERED, UNCOVERED):
        await db.execute(
            "INSERT INTO jobs (job_id, repo, context, task, status, pipeline_template, created_at, started_at, "
            "finished_at, pr_url, delivery_state, delivery_state_at, dispatched_by) VALUES ($1, NULL, $2, 'Done', "
            "'completed', 'full-magnum-opus', $3, $3, $4, $5, 'pr_open', $4, 'ada@example.com')",
            _done(repo),
            {"type": "git", "repo": repo},
            NOW - timedelta(minutes=20),
            NOW - timedelta(minutes=10),
            f"https://github.com/{repo}/pull/9",
        )
        await db.execute(
            "INSERT INTO traces (trace_id, job_id, agent_type, model, result, cost_usd, duration_ms, created_at) "
            "VALUES ($1, $2, 'developer', 'm', 'success', 1, 1000, $3)",
            f"tr-{_done(repo)}",
            _done(repo),
            NOW - timedelta(minutes=12),
        )
        await db.execute(
            "INSERT INTO jobs (job_id, repo, context, task, status, pipeline_template, created_at, started_at, "
            "dispatched_by) VALUES ($1, NULL, $2, 'Live', 'running', 'full-magnum-opus', $3, $3, 'ada@example.com')",
            _live(repo),
            {"type": "git", "repo": repo},
            NOW - timedelta(minutes=5),
        )


def _context_jobs(rows: list[dict[str, Any]], key: str = "job_id") -> dict[str, Any]:
    """``job_id -> repo`` of the context-only jobs in ``rows``."""
    return {
        str(r[key]).removeprefix("done:"): r.get("repo")
        for r in rows
        if str(r[key]).removeprefix("done:").startswith("job-context-")
    }


async def _seen(app: Any, token: str) -> dict[str, dict[str, Any]]:
    world = (await call(app, "GET", "/api/v1/world", token)).json()
    character = (await call(app, "GET", "/api/v1/world/characters/developer", token)).json()
    review = (await call(app, "GET", "/api/v1/decisions?stage=done", token)).json()
    return {
        "parties": _context_jobs(world["parties"]),
        "inbox": _context_jobs(world["inbox"]),
        "loot": _context_jobs(world["loot"]),
        "quest log": _context_jobs(character["quest_log"]),
        "quests to review": _context_jobs(review["decisions"], key="id"),
    }


async def test_a_key_sees_the_context_only_jobs_of_its_repositories_and_no_other(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _context_only_jobs(db)
        limited = await _seen(app, keys.limited)
        everyone = await _seen(app, keys.open)

    finished = {_done(COVERED): COVERED}
    assert limited == {
        "parties": {_live(COVERED): COVERED},
        "inbox": finished,
        "loot": finished,
        "quest log": finished,
        "quests to review": finished,
    }, f"the limited key sees its repository's context-only jobs, under that repository, and no other: {limited}"
    both = {_done(COVERED): COVERED, _done(UNCOVERED): UNCOVERED}
    assert everyone == {
        "parties": {_live(COVERED): COVERED, _live(UNCOVERED): UNCOVERED},
        "inbox": both,
        "loot": both,
        "quest log": both,
        "quests to review": both,
    }, f"a key without a list sees each, under its context's repository: {everyone}"


async def test_one_quest_and_its_decision_read_the_context_s_repository(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _context_only_jobs(db)
        answers = {
            repo: (
                await call(app, "GET", f"/api/v1/world/quests/{_done(repo)}", keys.limited),
                await call(app, "GET", f"/api/v1/decisions/done:{_done(repo)}", keys.limited),
            )
            for repo in (COVERED, UNCOVERED)
        }

    quest, decision = answers[COVERED]
    assert (quest.status_code, decision.status_code) == (200, 200), (quest.text, decision.text)
    assert (quest.json()["repo"], decision.json()["repo"]) == (COVERED, COVERED), (
        f"the quest and its decision name the job's repository: {quest.json()['repo']}, {decision.json()['repo']}"
    )
    assert [r.status_code for r in answers[UNCOVERED]] == [404, 404], "another repository's job is not there"


async def test_regions_and_slop_cleanups_count_a_job_under_its_context_s_repository(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    for job, status, coder in (("job-context-new", "running", None), ("job-context-clean", "pr_merged", SLOPMASTER)):
        await db.execute(
            "INSERT INTO jobs (job_id, repo, context, task, status, area, coding_agent, created_at, finished_at) "
            "VALUES ($1, NULL, $2, 'Task', $3, 'Docs', $4, $5, $5)",
            job,
            {"type": "git", "repo": "acme/docs"},
            status,
            coder,
            NOW - timedelta(hours=1),
        )
    world = WorldRepository(db)

    regions = {r["repo"]: r["jobs"] for r in await world.regions()}
    cleaned = await world.cleaned(days=30)

    assert regions.get("acme/docs") == 2, (
        f"a repository whose jobs name it in their context alone is a region: {regions}"
    )
    assert cleaned == {("acme/docs", "Docs"): 1}, f"a slop cleanup counts under its context's repository: {cleaned}"


# --- a row that names a repository keeps it ----------------------------------

#: The covered repository as a row may spell it: the allowlist matches it whatever its case.
MIXED_CASE = COVERED.upper()


async def _row_jobs(db: DatabasePool) -> None:
    """A finished and a running job whose row names the covered repository in another case, and a finished one whose
    row and context name different repositories."""
    for job, repo, context, status, finished in (
        ("job-row-done-mixed", MIXED_CASE, None, "completed", True),
        ("job-row-live-mixed", MIXED_CASE, None, "running", False),
        ("job-row-done-both", COVERED, {"type": "git", "repo": UNCOVERED}, "completed", True),
    ):
        await db.execute(
            "INSERT INTO jobs (job_id, repo, context, task, status, pipeline_template, created_at, started_at, "
            "finished_at, pr_url, delivery_state, delivery_state_at, dispatched_by) VALUES ($1, $2, $3, 'Row', $4, "
            "'full-magnum-opus', $5, $5, $6, $7, 'pr_open', $6, 'ada@example.com')",
            job,
            repo,
            context,
            status,
            NOW - timedelta(minutes=15),
            NOW - timedelta(minutes=8) if finished else None,
            f"https://github.com/{COVERED}/pull/{len(job)}" if finished else None,
        )


def _row_shown(rows: list[dict[str, Any]], key: str = "job_id") -> dict[str, Any]:
    return {
        str(r[key]).removeprefix("done:"): r.get("repo")
        for r in rows
        if str(r[key]).removeprefix("done:").startswith("job-row-")
    }


async def test_a_row_s_repository_wins_and_is_read_as_stored(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _row_jobs(db)
        world = (await call(app, "GET", "/api/v1/world", keys.limited)).json()
        review = (await call(app, "GET", "/api/v1/decisions?stage=done", keys.limited)).json()

    parties, inbox = _row_shown(world["parties"]), _row_shown(world["inbox"])
    assert parties == {"job-row-live-mixed": MIXED_CASE}, (
        f"a running job whose row names the covered repository in another case is the key's, spelled as stored: "
        f"{parties}"
    )
    assert inbox == {"job-row-done-mixed": MIXED_CASE, "job-row-done-both": COVERED}, (
        f"the inbox names each finished job's repository as its row stores it, the row's over its context's: {inbox}"
    )
    assert _row_shown(review["decisions"], key="id") == inbox, f"the quests to review agree: {review['decisions']}"


# --- the asks feed -------------------------------------------------------------


def test_the_shared_fragment_holds_no_brace() -> None:
    """The asks feed and the progression join it into text that goes through ``str.format``."""
    assert "{" not in job_repository_sql("j") and "}" not in job_repository_sql("j")


async def _asks(db: DatabasePool) -> dict[str, tuple[str, str]]:
    """For each repository's context-only finished job, a queued ask about its decision and its code."""
    made: dict[str, tuple[str, str]] = {}
    for repo in (COVERED, UNCOVERED):
        decision = f"done:{_done(repo)}"
        ask = f"ask-{_done(repo)}"
        await db.execute(
            "INSERT INTO asks (id, decision_id, recipient, state, due_at, created_at) "
            "VALUES ($1, $2, 'ada@example.com', 'queued', $3, $3)",
            ask,
            decision,
            NOW - timedelta(minutes=5),
        )
        made[repo] = (ask, await AsksRepository(db).code(decision))
    return made


async def _ask_answers(app: Any, token: str, ask: str, code: str) -> dict[str, Any]:
    listed = (await call(app, "GET", "/api/v1/asks", token)).json()["asks"]
    return {
        "listed": ask in {a["id"] for a in listed},
        "by code": (await call(app, "GET", f"/api/v1/asks/decision/{code}", token)).status_code,
        "answered": (
            await call(
                app,
                "POST",
                "/api/v1/asks/answer",
                token,
                {"code": code, "action": "later", "person": "ada@example.com"},
            )
        ).status_code,
    }


async def test_the_asks_about_a_context_only_job_stay_the_key_s_before_and_after_review(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _context_only_jobs(db)
        made = await _asks(db)
        (ask, code), (other_ask, other_code) = made[COVERED], made[UNCOVERED]
        repos = await AsksRepository(db).decision_repos([f"done:{_done(COVERED)}", f"done:{_done(UNCOVERED)}"])
        sent = await call(app, "POST", f"/api/v1/asks/{ask}/sent", keys.limited, {"message_ref": "m-1"})
        open_ = await _ask_answers(app, keys.limited, ask, code)
        open_other = await _ask_answers(app, keys.limited, other_ask, other_code)
        for repo in (COVERED, UNCOVERED):  # the person reviews both: they leave the quests to review
            await db.execute(
                "INSERT INTO job_ratings (job_id, rating, rated_by) VALUES ($1, 'good', 'ada@example.com')", _done(repo)
            )
        reviewed = await _ask_answers(app, keys.limited, ask, code)
        reviewed_other = await _ask_answers(app, keys.limited, other_ask, other_code)

    # 409 on an answer: the code reaches the decision, which takes no chat answer of this kind; never "no decision".
    reachable = {"listed": True, "by code": 200, "answered": 409}
    assert open_ == reachable, f"while it waits for review, the key reads its ask and reaches its decision: {open_}"
    assert reviewed == reachable, f"after review, the ask and its decision are still the key's: {reviewed}"
    assert repos == {f"done:{_done(COVERED)}": COVERED, f"done:{_done(UNCOVERED)}": UNCOVERED}, (
        f"an ask's decision is read back under the job's context's repository: {repos}"
    )
    assert sent.status_code == 200, f"the key may report its own ask sent: {sent.status_code} {sent.text}"
    hidden = {"listed": False, "by code": 404, "answered": 404}
    assert open_other == hidden and reviewed_other == hidden, (
        f"another repository's ask is not the key's: {open_other}, {reviewed_other}"
    )
