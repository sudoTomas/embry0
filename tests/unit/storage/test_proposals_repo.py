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


async def _audits(db, *rows: tuple[str, str, str, int | None]) -> dict[str, str]:
    """Context-audit jobs ``(job_id, repo, status, finished days ago)``; None for one that has not finished.

    Answers the audit whose findings each repository offers, repository to job id.
    """
    for job_id, repo, status, days_ago in rows:
        await db.execute(
            """
            INSERT INTO jobs (job_id, repo, task, status, pipeline_template, result_summary, created_at, finished_at)
            VALUES ($1, $2, 't', $3, 'context-audit', 'Stale: 1',
                    NOW() - make_interval(days => COALESCE($4, 0)::int, hours => 1),
                    NOW() - make_interval(days => $4::int))
            """,
            job_id,
            repo,
            status,
            days_ago,
        )
    return {a["repo"]: a["job_id"] for a in await ProposalsRepository(db).audits()}


async def test_a_merged_newest_audit_offers_no_findings_and_never_an_older_audits(db_with_migrations) -> None:
    """#217: its pull request may have fixed them, and any older audit's too, so none until the next audit."""
    offered = await _audits(
        db_with_migrations,
        ("job-old", "o/merged", "completed", 9),
        ("job-merged", "o/merged", "pr_merged", 2),
        # A newer audit that failed or has not finished is passed over: the merged one is still the newest.
        ("job-old-2", "o/merged-then-failed", "completed", 9),
        ("job-merged-2", "o/merged-then-failed", "pr_merged", 2),
        ("job-failed", "o/merged-then-failed", "failed", 1),
        ("job-running", "o/merged-then-failed", "running", None),
        # The next audit finished: its findings are offered.
        ("job-merged-3", "o/audited-again", "pr_merged", 5),
        ("job-next", "o/audited-again", "completed", 1),
    )
    assert "o/merged" not in offered, f"a merged audit's or an older audit's findings are offered: {offered}"
    assert "o/merged-then-failed" not in offered, f"an older audit's findings are offered: {offered}"
    assert offered == {"o/audited-again": "job-next"}, (
        f"the next audit's findings are offered once it finished: {offered}"
    )


async def test_a_closed_newest_audit_s_findings_stand(db_with_migrations) -> None:
    """#217: a pull request closed unmerged fixed nothing, so its audit's findings are still offered."""
    offered = await _audits(
        db_with_migrations,
        ("job-old", "o/closed", "completed", 9),
        ("job-closed", "o/closed", "pr_closed", 2),
    )
    assert offered == {"o/closed": "job-closed"}, f"a closed audit's own findings are offered: {offered}"


async def test_a_completed_newest_audit_is_offered_over_an_older_merged_one(db_with_migrations) -> None:
    """Unchanged by #217: the newest audit that finished its work is offered, a newer failed or running one passed over."""
    offered = await _audits(
        db_with_migrations,
        ("job-merged", "o/a", "pr_merged", 9),
        ("job-new", "o/a", "completed", 1),
        ("job-failed", "o/a", "failed", 0),
        ("job-running", "o/a", "running", None),
    )
    assert offered == {"o/a": "job-new"}, f"the newest completed audit's findings are offered: {offered}"


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
