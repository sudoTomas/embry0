"""The events progression reads, against the migrated schema: what earns experience and what never does."""

from __future__ import annotations

import pytest

from embry0.services.progression_pack import FactQuery, default_rules, fact_key
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
    """The healed, watched and scheduled sources were read and never priced: they are gone (#176)."""
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
            ("run", "review"),
            ("run", "developer"),
            ("asked", "triage"),
        ]
    ), "work no character does (healed cases, watcher runs, scheduled starts) is not an event"
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


async def test_the_facts_count_outcomes_with_their_evidence(db_with_migrations) -> None:
    db = db_with_migrations

    async def job(
        job_id: str,
        status: str,
        minutes: int,
        *,
        issue: str | None = None,
        cost: float = 0.0,
        pr: int | None = None,
        delivery: str = "none",
        template: str | None = None,
        ran_minutes: int = 10,
    ) -> None:
        await db.execute(
            """
            INSERT INTO jobs (job_id, repo, task, status, issue_id, total_cost_usd, pr_url, delivery_state,
                              pipeline_template, created_at, started_at, finished_at, delivery_state_at)
            VALUES ($1, 'o/r', 't', $2, $3, $4, $5, $6, $7,
                    TIMESTAMPTZ '2026-10-06 12:00Z' + make_interval(mins => $8),
                    TIMESTAMPTZ '2026-10-06 12:00Z' + make_interval(mins => $8),
                    TIMESTAMPTZ '2026-10-06 12:00Z' + make_interval(mins => $8 + $9),
                    TIMESTAMPTZ '2026-10-06 12:00Z' + make_interval(mins => $8 + $9 + 1))
            """,
            job_id,
            status,
            issue,
            cost,
            f"https://forge/o/r/pull/{pr}" if pr else None,
            delivery,
            template,
            minutes,
            ran_minutes,
        )

    await db.execute("INSERT INTO issues (id, title, repo) VALUES ('iss-1', 'Fix the login', 'o/r')")
    await job("job-first", "completed", 0, cost=0.5, pr=7, delivery="merged")  # the repo's first job
    await job("job-failed", "failed", 10, issue="iss-1")
    await job("job-merged", "pr_merged", 20, issue="iss-1", cost=1.5, pr=8, ran_minutes=180)
    await job("job-open", "completed", 30, pr=9, delivery="pr_open")
    await job("job-audit", "completed", 40, template="context-audit")
    await db.execute("UPDATE jobs SET result_summary = 'Stale: 3' || chr(10) || 'Files: 2' WHERE job_id = 'job-audit'")
    await _trace(db, "trc-err", "job-merged", "developer", error="ERR_AGENT_MAX_TURNS", result="error")
    await _trace(db, "trc-review", "job-merged", "review")

    rules = default_rules()
    facts = await ProgressionRepository(db).facts(rules.fact_queries, rules.pack.timezone)
    counted: dict[str, float] = {}
    for fact in facts:
        counted[fact.metric] = counted.get(fact.metric, 0) + fact.value
    assert counted == {
        "merged": 2,
        "first_try": 1,
        "clean_kill": 1,
        fact_key("merged_under_cost", {"max_usd": 1}): 1,
        fact_key("merged_within", {"hours": 24}): 2,
        "repo_hopper": 1,
        "issue_done": 1,
        "comeback": 1,
        fact_key("merged_running_over", {"hours": 2}): 1,
        "lucky_find": 1,
        "audit": 1,
        fact_key("reviews", {"agents": ["review", "pr-reviewer"]}): 1,
    }, counted
    merged = [f for f in facts if f.metric == "merged"]
    assert [(f.label, f.url) for f in merged] == [
        ("o/r #7", "https://forge/o/r/pull/7"),
        ("o/r #8", "https://forge/o/r/pull/8"),
    ], "an open pull request is not a merge; the evidence names the pull request"
    assert next(f for f in facts if f.metric == "issue_done").label == "Fix the login"
    assert next(f for f in facts if f.metric == "audit").report == "Stale: 3\nFiles: 2"
    events = await ProgressionRepository(db).events()
    assert [e.agent_type for e in events if e.source == "comeback"] == ["developer"], "experience still reads events"


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


async def _merge(db, job_id: str, at: str, *, cost: float = 0.0) -> None:
    await db.execute(
        """
        INSERT INTO jobs (job_id, repo, task, status, total_cost_usd, created_at, finished_at, delivery_state, delivery_state_at)
        VALUES ($1, 'o/r', 't', 'pr_merged', $2, $3::text::timestamptz - INTERVAL '1 hour', $3::text::timestamptz, 'merged',
                $3::text::timestamptz)
        """,
        job_id,
        cost,
        at,
    )


def _counts(facts) -> dict[str, float]:
    counted: dict[str, float] = {}
    for fact in facts:
        counted[fact.metric] = counted.get(fact.metric, 0) + fact.value
    return counted


async def test_a_packs_parameters_reach_the_facts_as_values(db_with_migrations) -> None:
    """#176: a metric's parameters are the pack's, bound in SQL; two settings of one metric count apart."""
    db = db_with_migrations
    await _merge(db, "j-cheap", "2026-10-06 12:00Z", cost=0.5)
    await _merge(db, "j-three", "2026-10-07 12:00Z", cost=3.0)
    for case, severity, seen in (("hc-a", "high", 3), ("hc-b", "low", 40)):
        await db.execute(
            "INSERT INTO healing_cases (id, repo, origin, severity, occurrence_count, healed_verdict, verified_at) "
            "VALUES ($1, 'o/r', 'watcher', $2, $3, 'healed', NOW())",
            case,
            severity,
            seen,
        )
    queries = [
        FactQuery(fact_key("merged_under_cost", {"max_usd": 1}), "merged_under_cost", {"max_usd": 1.0}),
        FactQuery(fact_key("merged_under_cost", {"max_usd": 5}), "merged_under_cost", {"max_usd": 5.0}),
        FactQuery("healed", "healed", {}),
        FactQuery(fact_key("healed", {"severity": "high"}), "healed", {"severity": "high"}),
        FactQuery(fact_key("healed", {"min_seen": 10}), "healed", {"min_seen": 10}),
    ]
    counted = _counts(await ProgressionRepository(db).facts(queries, "UTC"))
    assert counted == {
        fact_key("merged_under_cost", {"max_usd": 1}): 1,
        fact_key("merged_under_cost", {"max_usd": 5}): 2,
        "healed": 2,
        fact_key("healed", {"severity": "high"}): 1,
        fact_key("healed", {"min_seen": 10}): 1,
    }, counted


async def test_the_oddities_and_a_hot_streak_read_the_packs_calendar(db_with_migrations) -> None:
    """#176: a day and an hour are the pack's timezone's, not always UTC's."""
    db = db_with_migrations
    await _merge(db, "j-eve", "2026-12-24 23:30Z")  # 25 December, 00:30, in Berlin
    for i, at in enumerate(("2026-10-06 22:30Z", "2026-10-06 23:10Z", "2026-10-07 08:00Z")):
        await _merge(db, f"j-streak-{i}", at)  # three merges on 7 October in Berlin, two days in UTC
    queries = [
        FactQuery(fact_key("merged_on_dates", {"dates": ["12-25"]}), "merged_on_dates", {"dates": ["12-25"]}),
        FactQuery(fact_key("merges_in_a_day", {"count": 3}), "merges_in_a_day", {"count": 3}),
        FactQuery(
            fact_key("merged_at_local_time", {"weekdays": [5], "from_hour": 0, "to_hour": 4}),
            "merged_at_local_time",
            {"weekdays": [5], "from_hour": 0, "to_hour": 4},
        ),
    ]
    repo = ProgressionRepository(db)
    in_utc = _counts(await repo.facts(queries, "UTC"))
    in_berlin = _counts(await repo.facts(queries, "Europe/Berlin"))
    assert in_utc == {}, in_utc
    assert in_berlin == {
        fact_key("merged_on_dates", {"dates": ["12-25"]}): 1,
        fact_key("merges_in_a_day", {"count": 3}): 1,
        fact_key("merged_at_local_time", {"weekdays": [5], "from_hour": 0, "to_hour": 4}): 1,  # 00:30 on a Friday
    }, in_berlin
