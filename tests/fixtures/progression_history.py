"""A seeded history that exercises every progression source, metric, quest and diary task.

Used by the golden test (tests/unit/services/test_progression_golden.py),
which pins what ``GET /progression`` answers for it. Every timestamp is fixed,
so the same rows always read the same way. Names are neutral examples.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from embry0.storage.database import DatabasePool

BASE = datetime(2025, 10, 1, 12, tzinfo=UTC)  # a Wednesday


def t(day: float, hour: float = 0.0) -> datetime:
    """``day`` days and ``hour`` hours after the base (noon UTC on 1 October 2025)."""
    return BASE + timedelta(days=day, hours=hour)


AGENTS = (
    "developer",
    "review",
    "qa",
    "triage",
    "research",
    "ops",
    "onboarding",
    "design-auditor",
    "blast-radius",
    "pr-reviewer",
    "context-auditor",
    "slopmaster",
    "my-custom-agent",
)

#: A run's tool calls by the skill they train, as the classifier has stored them since migration 107.
HEAVY_READS = {"read:reading": 9_000, "search:searching": 400, "edit:coding": 300}
MIXED = {
    "edit:coding": 40,
    "edit:testing": 12,
    "edit:documentation": 6,
    "edit:databases": 3,
    "edit:design": 2,
    "edit:operations": 2,
    "shell:testing": 30,
    "shell:linting": 20,
    "shell:version_control": 25,
    "shell:building": 8,
    "shell:security": 3,
    "shell:databases": 2,
    "shell:operations": 4,
    "shell:shell": 50,
    "web:research": 6,
    "agent:delegation": 3,
    "plan:planning": 9,
    "browser:browsing": 14,
    "log:debugging": 7,
}
#: A run from before migration 107: only its tool names.
OLD_TOOLS = {
    "Bash": 30,
    "Edit": 25,
    "Read": 60,
    "Grep": 20,
    "WebFetch": 3,
    "Task": 2,
    "TodoWrite": 5,
    "mcp__playwright__browser_click": 4,
    "mcp__embry0__logs_query": 3,
    "Skill": 9,
}


async def _job(db: DatabasePool, job_id: str, **row: Any) -> None:
    row = {"repo": "acme/widgets", "task": "t", "status": "completed", "budget_overrun_usd": 0, **row}
    created = row.pop("at")
    ran = row.pop("ran_hours", 0.5)
    row.setdefault("created_at", created)
    row.setdefault("started_at", created)
    row.setdefault("finished_at", created + timedelta(hours=ran))
    row.setdefault("updated_at", row["finished_at"])
    if row.get("status") == "pr_merged" or row.get("delivery_state") == "merged":
        row.setdefault("delivery_state", "merged")
        row.setdefault("delivery_state_at", row["finished_at"] + timedelta(minutes=5))
    columns = ["job_id", *row]
    values = [job_id, *row.values()]
    placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 1))
    await db.execute(f"INSERT INTO jobs ({', '.join(columns)}) VALUES ({placeholders})", *values)


async def _trace(
    db: DatabasePool,
    trace_id: str,
    job_id: str,
    agent: str,
    at: datetime,
    *,
    actions: dict[str, int] | None = None,
    tools: dict[str, int] | None = None,
    result: str = "success",
    error: str | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO traces (trace_id, job_id, agent_type, model, result, error_code, tools_called, skill_actions, created_at)
        VALUES ($1, $2, $3, 'm', $4, $5, $6, $7, $8)
        """,
        trace_id,
        job_id,
        agent,
        result,
        error,
        tools or {},
        actions or {},
        at,
    )


async def seed_history(db: DatabasePool) -> None:
    """Write the history: jobs, runs, cases, answers, reviews, setups and a standard."""
    for agent in AGENTS:
        await db.execute(
            "INSERT INTO agent_definitions (type, description, model) VALUES ($1, 'd', 'm') ON CONFLICT (type) DO NOTHING",
            agent,
        )
    for issue, parent in (("iss-1", None), ("iss-2", "iss-1"), ("iss-3", None), ("iss-4", None)):
        await db.execute(
            "INSERT INTO issues (id, title, repo, parent_issue_id) VALUES ($1, $2, 'acme/widgets', $3)",
            issue,
            f"Issue {issue}",
            parent,
        )

    # A repository onboarded, then its first job and first merge (the onboarding quest).
    await _job(db, "j-onboard", repo="acme/new", pipeline_template="onboard", at=t(0))
    await _job(db, "j-new-1", repo="acme/new", at=t(1))
    await _job(db, "j-new-2", repo="acme/new", status="pr_merged", pr_url="https://forge/acme/new/pull/1", at=t(2))

    # An issue that failed, then merged after a resume and a long run, in an area.
    await _job(db, "j-f1", issue_id="iss-1", status="failed", at=t(3))
    await _job(
        db,
        "j-m1",
        issue_id="iss-1",
        status="pr_merged",
        pr_url="https://forge/acme/widgets/pull/11",
        total_cost_usd=1.5,
        area="Billing",
        resume_attempts=1,
        ran_hours=3,
        at=t(4),
    )
    await _trace(db, "trc-m1-dev", "j-m1", "developer", t(4, 1), actions=MIXED)
    await _trace(db, "trc-m1-err", "j-m1", "developer", t(4, 2), actions=MIXED, result="error", error="ERR_X")

    # A sub-issue merged within a day after a review and a QA run (the review-and-QA quest).
    await _job(
        db,
        "j-m2",
        issue_id="iss-2",
        status="pr_merged",
        pr_url="https://forge/acme/widgets/pull/12",
        total_cost_usd=0.4,
        at=t(5),
    )
    await _trace(db, "trc-m2-triage", "j-m2", "triage", t(5, 0.1))
    await _trace(db, "trc-m2-dev", "j-m2", "developer", t(5, 0.2), actions=HEAVY_READS)
    await _trace(db, "trc-m2-review", "j-m2", "review", t(5, 0.3), actions={"read:reading": 40})
    await _trace(db, "trc-m2-qa", "j-m2", "qa", t(5, 0.4), actions={"browser:browsing": 30})

    # Three attempts at one issue, the last surviving two restarts.
    await _job(db, "j-a1", issue_id="iss-3", status="failed", at=t(6))
    await _job(db, "j-a2", issue_id="iss-3", status="failed", at=t(7))
    await _job(
        db,
        "j-a3",
        issue_id="iss-3",
        status="pr_merged",
        pr_url="https://forge/acme/widgets/pull/13",
        resume_attempts=2,
        at=t(8),
    )
    await _trace(db, "trc-a3-dev", "j-a3", "developer", t(8, 0.5), actions=HEAVY_READS)

    # A slop cleanup that removed code.
    await _job(
        db,
        "j-slop",
        pipeline_template="slop-cleanup",
        status="pr_merged",
        pr_url="https://forge/acme/widgets/pull/14",
        work_nature={"cleanup": {"added": 10, "removed": 210}},
        at=t(9),
    )
    await _trace(db, "trc-slop", "j-slop", "slopmaster", t(9, 0.2), actions={"edit:coding": 30})

    # Context audits: one with outdated claims, one clean, one scheduled, one whose fix merged.
    await _job(db, "j-audit", pipeline_template="context-audit", result_summary="Stale: 16\nFiles: 6\n", at=t(10))
    await _job(
        db,
        "j-audit-clean",
        repo="acme/lib",
        pipeline_template="context-audit",
        result_summary="Stale: 0\nFiles: 4\n",
        at=t(11),
    )
    await _job(
        db,
        "j-audit-sched",
        pipeline_template="context-audit",
        dispatched_by="embry0:context-audit",
        result_summary="Stale: 2\nFiles: 1\n",
        at=t(12),
    )
    await _job(
        db,
        "j-audit-pr",
        pipeline_template="context-audit",
        status="pr_merged",
        pr_url="https://forge/acme/widgets/pull/15",
        at=t(13),
    )

    # A finding's fix job is no audit (#241): one merged and one with its pull request open, each started by its
    # owner's yes. Neither earns audit credit, in a repository no audit ran on; the merged one is a merged proposal.
    for job_id, status, delivery, pr, report, day in (
        ("j-fix-merged", "pr_merged", "merged", 2, "Stale: 1\nFiles: 1\n", 26),
        ("j-fix-open", "completed", "pr_open", 3, "Stale: 0\nFiles: 1\n", 27),
    ):
        await db.execute(
            "INSERT INTO proposal_decisions (proposal_id, repo, kind, decision, decided_by, job_id, created_at) "
            "VALUES ($1, 'acme/new', 'finding', 'accept', 'octocat', $2, $3)",
            f"finding:{job_id}",
            job_id,
            t(day, -1),
        )
        await _job(
            db,
            job_id,
            repo="acme/new",
            pipeline_template="context-audit",
            dispatched_by="embry0:proposals",
            status=status,
            delivery_state=delivery,
            pr_url=f"https://forge/acme/new/pull/{pr}",
            result_summary=report,
            at=t(day),
        )

    # QA, an area map, a follow-up, a gate refusal, a research, ops and design run.
    await _job(db, "j-qa", pipeline_template="qa", at=t(14))
    await _trace(db, "trc-qa", "j-qa", "qa", t(14, 0.1), actions={"browser:browsing": 50, "log:debugging": 5})
    await _job(
        db,
        "j-map",
        pipeline_template="map-areas",
        status="pr_merged",
        pr_url="https://forge/acme/widgets/pull/16",
        at=t(15),
    )
    await _job(
        db,
        "j-follow",
        pipeline_template="pr-followup",
        status="pr_merged",
        pr_url="https://forge/acme/widgets/pull/17",
        at=t(16),
    )
    await _job(db, "j-gate", status="failed", error_code="ERR_VERIFY_NEW_TEST_FAILURES", at=t(17))
    await _job(db, "j-other", at=t(18))
    for i, agent in enumerate(("research", "ops", "onboarding", "design-auditor", "blast-radius", "pr-reviewer")):
        await _trace(db, f"trc-other-{agent}", "j-other", agent, t(18, i * 0.1), actions={"read:reading": 20})
    await _trace(db, "trc-custom", "j-other", "my-custom-agent", t(18, 1), actions={"read:reading": 5})
    await _trace(db, "trc-old", "j-other", "developer", t(18, 2), tools=OLD_TOOLS)

    # Strange hours: a Friday night, a Saturday, two in the morning.
    await _job(db, "j-fri", status="pr_merged", pr_url="https://forge/acme/widgets/pull/20", at=t(2, 9))
    await _job(db, "j-sat", status="pr_merged", pr_url="https://forge/acme/widgets/pull/21", at=t(3, 2))
    await _job(db, "j-night", status="pr_merged", pr_url="https://forge/acme/widgets/pull/22", at=t(5, 14))

    # A hot streak: three merges on one day.
    for i in range(3):
        await _job(db, f"j-streak-{i}", status="pr_merged", pr_url=f"https://forge/acme/widgets/pull/3{i}", at=t(20, i))

    # The first job on another repository, merged; then enough merges for its harder diary tiers.
    await _job(db, "j-lib-first", repo="acme/lib", status="pr_merged", pr_url="https://forge/acme/lib/pull/1", at=t(21))
    for i in range(10):
        await _job(
            db,
            f"j-lib-{i}",
            repo="acme/lib",
            status="pr_merged",
            pr_url=f"https://forge/acme/lib/pull/{10 + i}",
            at=t(22 + i),
        )
        await _trace(db, f"trc-lib-review-{i}", f"j-lib-{i}", "review", t(22 + i, 0.2))

    # Holidays: 31 October, 25 December, 1 January; and the year's last and first runs.
    await _job(db, "j-harvest", status="pr_merged", pr_url="https://forge/acme/widgets/pull/40", at=t(30, 10))
    await _job(db, "j-xmas", status="pr_merged", pr_url="https://forge/acme/widgets/pull/41", at=t(85, 3))
    await _job(db, "j-newyear", status="pr_merged", pr_url="https://forge/acme/widgets/pull/42", at=t(92, 1))
    await _trace(db, "trc-eve", "j-xmas", "developer", t(91, 10), actions={"edit:coding": 2})
    await _trace(db, "trc-ny", "j-newyear", "qa", t(92, 0.2), actions={"browser:browsing": 2})

    # A merged job whose opened-but-not-merged sibling is not a merge.
    await _job(
        db,
        "j-open",
        status="completed",
        pr_url="https://forge/acme/widgets/pull/50",
        delivery_state="pr_open",
        at=t(93),
    )

    # Production errors: one healed for good, one that came back, one deployed and not healed.
    await db.execute(
        """
        INSERT INTO healing_cases (id, repo, origin, title, tracker_url, severity, occurrence_count, healed_verdict,
                                   accepted_at, merged_at, deployed_at, verified_at, resolution_path)
        VALUES ('hc-1', 'acme/widgets', 'watcher', 'Timeout in checkout', 'https://tracker/1', 'high', 120, 'healed',
                $1, $2, $3, $4, 'embry0')
        """,
        t(40),
        t(41),
        t(42),
        t(43),
    )
    await db.execute(
        """
        INSERT INTO healing_cases (id, repo, origin, title, tracker_url, severity, occurrence_count, healed_verdict,
                                   verdict_checked_at, deployed_at)
        VALUES ('hc-2', 'acme/widgets', 'watcher', 'Null sender', 'https://tracker/2', 'low', 12, 'healed', $1, $2)
        """,
        t(45),
        t(44),
    )
    await db.execute(
        "INSERT INTO healing_case_events (case_id, event, to_stage, occurred_at) VALUES ('hc-2', 'recurrence', 'verified', $1)",
        t(46),
    )
    await db.execute(
        "INSERT INTO healing_case_events (case_id, event, to_stage, occurred_at, detail) "
        "VALUES ('hc-1', 'verdict_recorded', 'verified', $1, $2::jsonb)",
        t(43),
        {"updates": {"healed_verdict": "healed"}},
    )
    await db.execute(
        "INSERT INTO healing_cases (id, repo, origin, title, healed_verdict, deployed_at) "
        "VALUES ('hc-3', 'acme/widgets', 'human', 'Flaky upload', 'not_healed', $1)",
        t(47),
    )
    await db.execute(
        "INSERT INTO watcher_runs (id, window_start, window_end, action, log_lines, created_at) "
        "VALUES ('wr-1', $1, $1, 'no_issue', 40, $1)",
        t(48),
    )

    # Owners' answers, questions asked and answered, reviews posted, a setup applied, a standard met.
    for i, (decision, job_id) in enumerate((("accept", "j-m2"), ("decline", None), ("snooze", None))):
        await db.execute(
            "INSERT INTO proposal_decisions (proposal_id, repo, kind, decision, decided_by, job_id, created_at) "
            "VALUES ($1, 'acme/widgets', 'issue', $2, 'octocat', $3, $4)",
            f"issue:{i}",
            decision,
            job_id,
            t(50 + i),
        )
    await db.execute(
        "INSERT INTO issue_inputs (id, job_id, question, answer, answered_at) VALUES ('in-1', 'j-m1', $1, 'yes', $2)",
        "Should the retry stay?",
        t(4, 1),
    )
    await db.execute("INSERT INTO issue_inputs (id, job_id, question) VALUES ('in-2', 'j-m1', 'Unanswered?')")
    for actor, at in (("triage", t(4, 0.5)), ("developer", t(8, 0.2))):
        await db.execute(
            "INSERT INTO audit_log (action, actor, created_at) VALUES ('issue.input_created', $1, $2)", actor, at
        )
    await db.execute(
        "INSERT INTO pr_reviews (id, repo, number, head_sha, trigger, trigger_key, posted_at) "
        "VALUES ('rv-1', 'acme/widgets', 7, 'abc', 'ready', 'k-1', $1)",
        t(55),
    )
    await db.execute(
        "INSERT INTO repo_setup_proposals (id, repo, status, decided_at) VALUES ('sp-1', 'acme/widgets', 'applied', $1)",
        t(56),
    )
    await db.execute(
        "INSERT INTO repo_standard_state (repo, owner, kind, score, last_audit_at) "
        "VALUES ('acme/widgets', 'acme', 'design', 95, $1)",
        t(57),
    )
