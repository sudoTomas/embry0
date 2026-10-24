"""The rows owner proposals read, against the migrated schema: what is asked and what is not."""

from __future__ import annotations

import pytest

from embry0.storage.repositories.proposals import ProposalsRepository

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def test_the_cases_are_tracked_watcher_findings_on_enabled_stacks(db_with_migrations) -> None:
    db = db_with_migrations
    await db.execute(
        "INSERT INTO watcher_stacks (repo, enabled, logql) VALUES ('o/watched', TRUE, 'q'), ('o/off', FALSE, 'q')"
    )
    for case_id, repo, origin, stage, key in (
        ("hc-yes", "o/watched", "watcher", "tracked", "T-1"),
        ("hc-detected", "o/watched", "watcher", "detected", None),
        ("hc-off", "o/off", "watcher", "tracked", "T-2"),
        ("hc-org", "o/watched", "org", "tracked", "T-3"),
    ):
        await db.execute(
            """
            INSERT INTO healing_cases (id, repo, origin, stage, tracker, tracker_key, title)
            VALUES ($1, $2, $3, $4, CASE WHEN $5::text IS NULL THEN NULL ELSE 'github' END, $5, 't')
            """,
            case_id,
            repo,
            origin,
            stage,
            key,
        )
    assert [c["id"] for c in await ProposalsRepository(db).cases()] == ["hc-yes"]


async def test_the_issues_are_open_with_no_job(db_with_migrations) -> None:
    db = db_with_migrations
    for issue_id, status in (("iss-new", "open"), ("iss-started", "open"), ("iss-closed", "closed")):
        await db.execute("INSERT INTO issues (id, title, repo, status) VALUES ($1, 't', 'o/r', $2)", issue_id, status)
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, issue_id) VALUES ('job-1', 'o/r', 't', 'running', 'iss-started')"
    )
    assert [i["id"] for i in await ProposalsRepository(db).issues()] == ["iss-new"]


async def test_each_repo_s_newest_completed_audit(db_with_migrations) -> None:
    db = db_with_migrations
    for job_id, repo, status, days_ago in (
        ("job-old", "o/a", "completed", 9),
        ("job-new", "o/a", "completed", 1),
        ("job-failed", "o/a", "failed", 0),
        ("job-b", "o/b", "completed", 3),
    ):
        await db.execute(
            """
            INSERT INTO jobs (job_id, repo, task, status, pipeline_template, result_summary, finished_at)
            VALUES ($1, $2, 't', $3, 'context-audit', 'Stale: 0', NOW() - make_interval(days => $4))
            """,
            job_id,
            repo,
            status,
            days_ago,
        )
    assert {a["repo"]: a["job_id"] for a in await ProposalsRepository(db).audits()} == {
        "o/a": "job-new",
        "o/b": "job-b",
    }


async def test_owners_and_the_newest_answer(db_with_migrations) -> None:
    db = db_with_migrations
    await db.execute(
        "INSERT INTO repo_preferences (repo, owners) VALUES ('Acme/App', '[\"octo\"]'::jsonb), ('acme/none', NULL)"
    )
    repo = ProposalsRepository(db)
    assert await repo.owners() == {"acme/app": ["octo"]}
    await repo.record(proposal_id="issue:i-1", repo="o/r", kind="issue", decision="snooze", decided_by="octo")
    await repo.record(
        proposal_id="issue:i-1", repo="o/r", kind="issue", decision="accept", decided_by="octo", job_id="job-9"
    )
    decision = (await repo.decisions())["issue:i-1"]
    assert decision.decision == "accept" and decision.job_id == "job-9" and decision.decided_by == "octo"
