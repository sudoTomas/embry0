"""The progression credits a job to its repository as the jobs routes read it: its ``repo``, else its git context's.

``POST /jobs`` stores the repository a git context names, and migration 141
filled the rows written before it; a row that still names none, and whose
context does, belongs to that repository (``jobs.job_repository_sql``). The
journal (diaries, quests) and every fact that names a repository read it the
same way; a row that names a repository keeps it over its context's.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.services.progression_pack import FactQuery
from embry0.storage.repositories.progression import QA_PIPELINE, ProgressionRepository
from tests.fixtures.builtin_rows import planned_credit

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

START = datetime(2026, 10, 6, 12, tzinfo=UTC)
DOCS = "acme/docs"
TOOLS = "acme/tools"


def _context(repo: str) -> dict[str, str]:
    return {"type": "git", "repo": repo}


async def _job(
    db: Any, job_id: str, *, minutes: int, repo: str | None = None, context: str | None = None, **cols: Any
) -> None:
    """A job created ``minutes`` after a fixed start and finished ten minutes later, with ``cols`` set too."""
    created = START + timedelta(minutes=minutes)
    row = {
        "job_id": job_id,
        "repo": repo,
        "context": _context(context) if context else None,
        "task": "t",
        "created_at": created,
        "started_at": created,
        "finished_at": created + timedelta(minutes=10),
        **cols,
    }
    marks = ", ".join(f"${i}" for i in range(1, len(row) + 1))
    await db.execute(f"INSERT INTO jobs ({', '.join(row)}) VALUES ({marks})", *row.values())


async def _trace(db: Any, trace_id: str, job_id: str, agent: str) -> None:
    await db.execute(
        "INSERT INTO traces (trace_id, job_id, agent_type, model, result) VALUES ($1, $2, $3, 'm', 'success')",
        trace_id,
        job_id,
        agent,
    )


async def test_the_journal_credits_a_context_only_job_to_its_context_s_repository(db_with_migrations: Any) -> None:
    db = db_with_migrations
    await _job(db, "job-context", minutes=0, context=DOCS, status="completed")
    await _job(db, "job-both", minutes=1, repo=TOOLS, context=DOCS, status="completed")
    await _job(db, "job-none", minutes=2, status="completed")
    await _trace(db, "trc-context", "job-context", "developer")
    await _trace(db, "trc-both", "job-both", "developer")

    journal = await ProgressionRepository(db).journal()

    assert sorted(j.repo for j in journal.jobs) == [DOCS, TOOLS], (
        f"the diaries see the context-only job under its context's repository, the row's over the context's, and no "
        f"job with neither: {[j.repo for j in journal.jobs]}"
    )
    assert sorted(r.repo for r in journal.runs) == [DOCS, TOOLS], (
        f"each run is credited to its job's repository: {[r.repo for r in journal.runs]}"
    )


def _queries() -> list[FactQuery]:
    return [
        FactQuery("merged", "merged", {}),
        FactQuery("repo_hopper", "repo_hopper", {}),
        FactQuery("areas_mapped", "areas_mapped", {}),
        FactQuery("lucky_find", "lucky_find", {}),
        FactQuery("reviews", "reviews", {"agents": ["review"]}),
        FactQuery("qa_passed", "qa_passed", {}),
        FactQuery("gate_blocked", "gate_blocked", {}),
        FactQuery("resumed", "resumed", {"min_resumes": 2}),
        FactQuery("audit", "audit", {}),
    ]


async def test_every_fact_that_names_a_repository_names_the_context_s(db_with_migrations: Any) -> None:
    db = db_with_migrations
    pr = f"https://forge/{DOCS}/pull/7"
    # The repository's first job: merged, reviewed, and mapping its areas.
    await _job(
        db, "job-merged", minutes=0, context=DOCS, status="pr_merged", delivery_state="merged",
        pr_url=pr, pipeline_template="map-areas",
    )  # fmt: skip
    await _trace(db, "trc-review", "job-merged", "review")
    await _job(db, "job-qa", minutes=10, context=DOCS, status="completed", pipeline_template=QA_PIPELINE)
    await _job(db, "job-gate", minutes=20, context=DOCS, status="failed", error_code="ERR_VERIFY_NEW_TEST_FAILURES")
    await _job(db, "job-resumed", minutes=30, context=DOCS, status="completed", resume_attempts=3)
    audit = planned_credit("context-audit")
    await _job(
        db, "job-audit", minutes=40, context=DOCS, status="completed", pipeline_template="context-audit",
        leader_agent=audit["leader_agent"], coding_agent=audit["coding_agent"], result_summary="Stale: 0",
    )  # fmt: skip

    facts = await ProgressionRepository(db).facts(_queries(), "UTC")

    labels: dict[str, list[str]] = {}
    for fact in facts:
        labels.setdefault(fact.metric, []).append(fact.label)
    assert labels == {
        "merged": [f"{DOCS} #7"],
        "repo_hopper": [DOCS],
        "areas_mapped": [DOCS],
        "lucky_find": [f"{DOCS} #7"],
        "reviews": [DOCS],
        "qa_passed": [DOCS],
        "gate_blocked": [DOCS],
        "resumed": [DOCS],
        "audit": [DOCS],
    }, f"each fact names the context's repository: {labels}"


async def test_a_context_only_job_is_an_earlier_job_of_its_repository(db_with_migrations: Any) -> None:
    """A lucky find is a repository's first job merging: an earlier job that names it only in its context counts."""
    db = db_with_migrations
    await _job(db, "job-earlier", minutes=0, context=TOOLS, status="failed")
    await _job(
        db, "job-later", minutes=10, repo=TOOLS, status="pr_merged", delivery_state="merged",
        pr_url=f"https://forge/{TOOLS}/pull/3",
    )  # fmt: skip

    facts = await ProgressionRepository(db).facts([FactQuery("lucky_find", "lucky_find", {})], "UTC")

    assert [f.label for f in facts] == [], f"the merge was not the repository's first job: {facts}"
