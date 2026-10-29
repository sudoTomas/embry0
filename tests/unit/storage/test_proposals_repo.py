"""The rows owner proposals read, against the migrated schema: what is asked and what is not."""

from __future__ import annotations

from typing import NamedTuple

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

    A pull request that merged or closed did so, by the forge's clock, when its audit finished.
    Answers the audit whose findings each repository offers, repository to job id.
    """
    for job_id, repo, status, days_ago in rows:
        await db.execute(
            """
            INSERT INTO jobs (job_id, repo, task, status, pipeline_template, result_summary, created_at, finished_at,
                              delivery_state, delivery_state_at, updated_at)
            VALUES ($1, $2, 't', $3, 'context-audit', 'Stale: 1',
                    NOW() - make_interval(days => COALESCE($4, 0)::int, hours => 1),
                    NOW() - make_interval(days => $4::int),
                    CASE $3 WHEN 'pr_merged' THEN 'merged' WHEN 'pr_closed' THEN 'closed' ELSE 'none' END,
                    CASE WHEN $3 IN ('pr_merged', 'pr_closed') THEN NOW() - make_interval(days => $4::int) END,
                    COALESCE(NOW() - make_interval(days => $4::int), NOW()))
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


#: Who the proposals route dispatches a finding's fix job as.
FIX_BY = "embry0:proposals"


class _Run(NamedTuple):
    """One context-audit job of ``repo``, its times in hours before now."""

    job_id: str
    status: str
    started: int
    finished: int
    #: When its pull request merged or closed, by the forge's clock; None for one that did neither.
    settled: int | None = None
    by: str = "embry0:context-audit"
    repo: str = "o/r"
    #: False for a merge or close merge sync recorded without the forge's time, at ``settled``.
    forge_time: bool = True


async def _offered_of(db, *runs: _Run) -> str | None:
    """The audit whose findings ``o/r`` offers, after ``runs``; None for none."""
    for run in runs:
        delivery = {"pr_merged": "merged", "pr_closed": "closed"}.get(run.status, "none")
        await db.execute(
            """
            INSERT INTO jobs (job_id, repo, task, status, pipeline_template, dispatched_by, result_summary,
                              created_at, finished_at, delivery_state, delivery_state_at, updated_at)
            VALUES ($1, $2, 't', $3, 'context-audit', $4, 'Stale: 1',
                    NOW() - make_interval(hours => $5), NOW() - make_interval(hours => $6),
                    $7, CASE WHEN $9 THEN NOW() - make_interval(hours => $8::int) END,
                    NOW() - make_interval(hours => COALESCE($8::int, $6)))
            """,
            run.job_id,
            run.repo,
            run.status,
            run.by,
            run.started,
            run.finished,
            delivery,
            run.settled,
            run.forge_time,
        )
    return {a["repo"]: a["job_id"] for a in await ProposalsRepository(db).audits()}.get("o/r")


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        pytest.param([_Run("a1", "completed", 10, 9)], "a1", id="newest-completed"),
        pytest.param([_Run("a1", "pr_merged", 10, 9, 8)], None, id="newest-merged"),
        pytest.param(
            [_Run("a1", "pr_merged", 10, 9, 8), _Run("a2", "completed", 7, 6)],
            "a2",
            id="started-after-the-merge",
        ),
        # #236, the first order: a2 started before a1's pull request merged, and finished after it.
        pytest.param(
            [_Run("a1", "pr_merged", 10, 9, 5), _Run("a2", "completed", 7, 3)],
            None,
            id="started-before-an-earlier-audit-merged",
        ),
        # #236, the second order: a1 started before a2, which merged, and finished after it.
        pytest.param(
            [_Run("a1", "completed", 10, 3), _Run("a2", "pr_merged", 8, 6, 5)],
            None,
            id="started-before-the-merged-audit",
        ),
        pytest.param(
            [_Run("a1", "pr_merged", 10, 9, 5), _Run("a2", "completed", 7, 3), _Run("a3", "completed", 2, 1)],
            "a3",
            id="the-next-audit-after-the-merge",
        ),
        pytest.param(
            [_Run("a1", "pr_closed", 10, 9, 5), _Run("a2", "completed", 7, 3)],
            "a2",
            id="closed-unmerged-while-it-ran",
        ),
        pytest.param(
            [_Run("a1", "completed", 10, 3), _Run("x1", "pr_merged", 8, 6, 5, repo="o/other")],
            "a1",
            id="another-repository-merged-while-it-ran",
        ),
        # Without the forge's time a merge counts from when merge sync recorded it, which is no earlier
        # than the merge, so an unknown time can only offer less.
        pytest.param(
            [_Run("a1", "pr_merged", 10, 9, 5, forge_time=False), _Run("a2", "completed", 7, 3)],
            None,
            id="merged-with-no-forge-time-while-it-ran",
        ),
        # #240: a fix job's merge changed the one claim its owner accepted, not the audit's others.
        pytest.param(
            [_Run("a1", "completed", 10, 9), _Run("fix", "pr_merged", 8, 6, 5, by=FIX_BY)],
            "a1",
            id="a-fix-job-merged-while-it-ran",
        ),
    ],
)
async def test_an_audit_a_merge_overlapped_offers_no_findings(db_with_migrations, runs, expected) -> None:
    """#236: a merge after an audit started may have fixed what it found, whichever audit's pull request it was."""
    offered = await _offered_of(db_with_migrations, *runs)
    assert offered == expected, f"offered {offered!r}, expected {expected!r}, after {runs}"


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


_AUDIT_REPORT = """Stale: 2
Files: 1

## Findings

| Severity | Location | Claim | Evidence | Fix |
|---|---|---|---|---|
| high | `CLAUDE.md:3` | Run make check | There is no Makefile | Run the test script |
| low | `CLAUDE.md:9` | Node 18 is required | The version file says 22 | Node 22 is required |
"""

# What a fix job of the first claim reports: that claim, which it fixed.
_FIX_REPORT = """Stale: 1
Files: 1

## Findings

| Severity | Location | Claim | Evidence | Fix |
|---|---|---|---|---|
| high | `CLAUDE.md:3` | Run make check | There is no Makefile | Run the test script |
"""


async def _offered_with_a_fix_job(db, fix_status: str) -> tuple[str, list[tuple[str, str]]]:
    """An audit with two findings, a day later a fix job of the first one that reached ``fix_status``.

    Answers the audit's id and each finding the feed offers, its claim and the audit it came from.
    """
    from types import SimpleNamespace

    from embry0.api.v1.proposals import open_proposals
    from embry0.services.proposals import finding_id

    for job_id, by, status, report, days_ago in (
        ("job-audit", "embry0:context-audit", "completed", _AUDIT_REPORT, 2),
        ("job-fix", FIX_BY, fix_status, _FIX_REPORT, 1),
    ):
        await db.execute(
            """
            INSERT INTO jobs (job_id, repo, task, status, pipeline_template, dispatched_by, result_summary,
                              created_at, finished_at)
            VALUES ($1, 'o/r', 't', $2, 'context-audit', $3, $4,
                    NOW() - make_interval(days => $5, hours => 1), NOW() - make_interval(days => $5))
            """,
            job_id,
            status,
            by,
            report,
            days_ago,
        )
    repo = ProposalsRepository(db)
    # The owner's yes that started the fix job.
    await repo.record(
        proposal_id=finding_id("o/r", "CLAUDE.md", "Run make check"),
        repo="o/r",
        kind="finding",
        decision="accept",
        decided_by="octo",
        job_id="job-fix",
    )
    proposals = await open_proposals(SimpleNamespace(proposals_repo=repo, leads_repo=None))
    return "job-audit", [(p.extra["claim"], p.extra["audit_job_id"]) for p in proposals if p.kind == "finding"]


async def test_a_completed_fix_job_leaves_the_audit_s_other_findings_offered(db_with_migrations) -> None:
    """#222: a finding's fix job is no audit, so the audit before it is still the one whose findings are offered,
    less the one its owner accepted."""
    audit, offered = await _offered_with_a_fix_job(db_with_migrations, "completed")
    assert offered == [("Node 18 is required", audit)], (
        f"a completed fix job took the audit's place in the feed: {offered}"
    )


async def test_a_fix_job_whose_pull_request_merged_leaves_the_audit_s_other_findings_offered(
    db_with_migrations,
) -> None:
    """#222: its merge fixed the one claim its owner accepted, not the audit's others, so they are still offered."""
    audit, offered = await _offered_with_a_fix_job(db_with_migrations, "pr_merged")
    assert offered == [("Node 18 is required", audit)], (
        f"a fix job's merged pull request was read as the audit's: {offered}"
    )
