"""The events progression reads, against the migrated schema: what earns experience and what never does."""

from __future__ import annotations

import pytest

from embry0.storage.repositories.progression import ProgressionRepository

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def _job(
    db,
    job_id: str,
    *,
    status: str,
    template: str | None = None,
    by: str | None = None,
    error: str | None = None,
    report: str | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO jobs (job_id, repo, task, status, pipeline_template, dispatched_by, error_code, result_summary,
                          finished_at, budget_overrun_usd)
        VALUES ($1, 'o/r', 't', $2, $3, $4, $5, $6, NOW(), 0)
        """,
        job_id,
        status,
        template,
        by,
        error,
        report,
    )


async def _trace(
    db,
    trace_id: str,
    job_id: str,
    agent: str,
    error: str | None = None,
    *,
    result: str = "success",
    tools: dict | None = None,
    actions: dict | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO traces (trace_id, job_id, agent_type, model, result, error_code, tools_called, skill_actions)
        VALUES ($1, $2, $3, 'm', $4, $5, $6, $7)
        """,
        trace_id,
        job_id,
        agent,
        result,
        error,
        tools or {},  # the pool's jsonb codec encodes it
        actions or {},
    )


async def test_every_source_yields_its_events_and_nothing_else(db_with_migrations) -> None:
    db = db_with_migrations
    await _job(db, "job-merged", status="pr_merged")
    await db.execute("UPDATE jobs SET pr_url = 'https://forge/pr/1' WHERE job_id = 'job-merged'")
    await _job(db, "job-qa", status="completed", template="qa")
    await _job(db, "job-qa-failed", status="failed", template="qa")
    await _job(db, "job-audit", status="completed", template="context-audit", report="Stale: 3\nFiles: 1\n")
    await _job(db, "job-sched", status="completed", template="context-audit", by="embry0:context-audit")
    await _job(
        db,
        "job-refused",
        status="failed",
        template="context-audit",
        by="embry0:context-audit",
        error="ERR_BUDGET_REFUSED",
    )
    await _job(db, "job-run", status="completed")
    await _trace(db, "trc-review", "job-run", "review")
    await _trace(db, "trc-review-err", "job-run", "review", error="ERR_AGENT_MAX_TURNS")
    await _trace(db, "trc-running", "job-run", "review", result="running")  # not finished: nothing yet
    await _trace(db, "trc-dev", "job-run", "developer", tools={"Bash": 2}, actions={"shell:testing": 2})
    await db.execute("INSERT INTO audit_log (action, actor) VALUES ('issue.input_created', 'triage')")
    await db.execute("INSERT INTO healing_cases (id, repo, origin) VALUES ('hc-1', 'o/r', 'watcher')")
    for verdict in ("healed", "not_healed"):
        await db.execute(
            "INSERT INTO healing_case_events (case_id, event, to_stage, detail) VALUES ('hc-1', 'verdict_recorded', 'verified', $1::jsonb)",
            {"updates": {"healed_verdict": verdict}},  # the pool's jsonb codec encodes it
        )
    for run_id, lines in (("wr-read", 40), ("wr-quiet", 0)):
        await db.execute(
            "INSERT INTO watcher_runs (id, window_start, window_end, action, log_lines) VALUES ($1, NOW(), NOW(), 'no_issue', $2)",
            run_id,
            lines,
        )

    events = await ProgressionRepository(db).events()
    got = sorted((e.source, e.agent_type) for e in events)
    assert got == sorted(
        [
            ("merged", "developer"),
            ("opened", "developer"),
            ("qa_passed", "qa"),
            ("audited", "context-auditor"),
            ("audited", "context-auditor"),  # the scheduled audit completed too
            ("scheduled", None),
            ("run", "review"),
            ("run", "developer"),
            ("asked", "triage"),
            ("healed", None),
            ("watched", None),
        ]
    ), got
    audit = next(e for e in events if e.source == "audited" and e.report)
    assert audit.report == "Stale: 3\nFiles: 1\n", "the report is read for the claims bonus"
    developer_run = next(e for e in events if e.source == "run" and e.agent_type == "developer")
    assert developer_run.tools_called == {"Bash": 2} and developer_run.skill_actions == {"shell:testing": 2}
    assert [e.at for e in events] == sorted(e.at for e in events), "oldest first"


async def test_the_agents_are_every_defined_type(db_with_migrations) -> None:
    db = db_with_migrations
    await db.execute("INSERT INTO agent_definitions (type, description, model) VALUES ('zz-custom', 'd', 'm')")
    types = await ProgressionRepository(db).agent_types()
    assert "zz-custom" in types and types == sorted(types)


async def test_the_facts_achievements_count(db_with_migrations) -> None:
    db = db_with_migrations

    async def job(
        job_id: str, status: str, seconds: int, *, issue: str | None = None, cost: float = 0.0, minutes_ago: int = 0
    ) -> None:
        await db.execute(
            """
            INSERT INTO jobs (job_id, repo, task, status, issue_id, total_cost_usd, created_at, started_at, finished_at)
            VALUES ($1, 'o/r', 't', $2, $3, $4, NOW() - make_interval(mins => $5),
                    NOW() - make_interval(mins => $5), NOW() - make_interval(mins => $5) + make_interval(secs => $6))
            """,
            job_id,
            status,
            issue,
            cost,
            minutes_ago,
            seconds,
        )

    await db.execute("INSERT INTO issues (id, title, repo) VALUES ('iss-1', 't', 'o/r')")
    await job("job-quick", "completed", 5, cost=0.25)
    await job("job-slow", "completed", 300)
    await job("job-failed", "failed", 60, issue="iss-1", minutes_ago=10)
    await job("job-merged", "pr_merged", 600, issue="iss-1", cost=1.5)
    await job("job-cancelled", "cancelled", 3)
    await db.execute("UPDATE jobs SET resume_attempts = 1 WHERE job_id IN ('job-slow', 'job-cancelled')")
    await _trace(db, "trc-slow", "job-slow", "qa")

    repo = ProgressionRepository(db)
    counted: dict[str, float] = {}
    for fact in await repo.facts():
        counted[fact.metric] = counted.get(fact.metric, 0) + fact.value
    assert counted == {"completed": 3, "spent": 1.75, "quick": 1}, counted
    events = await repo.events()
    assert sorted((e.source, e.agent_type) for e in events if e.source in ("comeback", "resumed")) == [
        ("comeback", "developer"),
        ("resumed", "qa"),
    ], "a merge after a failure, and a resumed job credited to its last run's agent; a cancelled one is not"


async def test_the_journal_is_every_job_and_every_successful_run_with_its_repo(db_with_migrations) -> None:
    db = db_with_migrations
    await db.execute("INSERT INTO issues (id, title, repo) VALUES ('iss-j', 't', 'o/r')")
    await _job(db, "job-audit", status="completed", template="context-audit", report="Stale: 0\nFiles: 2\n")
    await _job(db, "job-fixed", status="pr_merged")
    await _job(db, "job-broken", status="failed", report="ignored: not an audit")
    await db.execute("UPDATE jobs SET issue_id = 'iss-j' WHERE job_id IN ('job-fixed', 'job-broken')")
    await db.execute("INSERT INTO jobs (job_id, repo, task, status) VALUES ('job-norepo', NULL, 't', 'completed')")
    await _trace(db, "trc-qa", "job-fixed", "qa")
    await _trace(db, "trc-qa-err", "job-fixed", "qa", error="ERR_AGENT_MAX_TURNS")
    await _trace(db, "trc-running", "job-fixed", "review", result="running")

    journal = await ProgressionRepository(db).journal()
    rows = {(j.template, j.status, j.issue_id, j.report) for j in journal.jobs}
    assert rows == {
        ("context-audit", "completed", None, "Stale: 0\nFiles: 2\n"),
        (None, "pr_merged", "iss-j", None),
        (None, "failed", "iss-j", None),
    }, "no repo-less job, and a report only for a completed audit"
    assert [(r.agent_type, r.repo, r.issue_id) for r in journal.runs] == [("qa", "o/r", "iss-j")]
