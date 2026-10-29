"""The events progression reads, against the migrated schema: what earns experience and what never does."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.services.merge_sync.recorder import aligned_status
from embry0.services.progression_pack import FactQuery, default_rules, fact_key
from embry0.services.progression_vocabulary import METRICS
from embry0.storage.repositories.job_delivery import DeliveryWrite, JobDeliveryRepository
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


def _query(metric: str, params: dict | None = None) -> FactQuery:
    """A metric's query as the rules build it: every parameter, its defaults included."""
    model = METRICS[metric].params.model_validate(params or {})
    return FactQuery(fact_key(metric, params or {}), METRICS[metric].fragment or "", model.model_dump(mode="json"))


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
        _query("merged_under_cost", {"max_usd": 1}),
        _query("merged_under_cost", {"max_usd": 5}),
        _query("healed"),
        _query("healed", {"severity": "high"}),
        _query("healed", {"min_seen": 10}),
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
        _query("merged_on_dates", {"dates": ["12-25"]}),
        _query("merges_in_a_day", {"count": 3}),
        _query("merged_at_local_time", {"weekdays": [5], "from_hour": 0, "to_hour": 4}),
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


# ---- the shipped thresholds sit exactly where they did (review of #207, finding 3) ----

MONDAY = datetime(2026, 1, 5, tzinfo=UTC)  # in January, New York is UTC-5 all month


async def _job_at(
    db,
    job_id: str,
    merged: datetime,
    *,
    created: timedelta = timedelta(days=30),
    ran: timedelta = timedelta(hours=1),
    cost: float = 0.0,
    issue: str | None = None,
    status: str = "pr_merged",
    resumes: int = 0,
    repo: str = "o/r",
) -> None:
    """A job that ended at ``merged`` (merged, unless ``status`` says otherwise), created ``created`` before."""
    await db.execute(
        """
        INSERT INTO jobs (job_id, repo, task, status, issue_id, total_cost_usd, resume_attempts, created_at, started_at,
                          finished_at, delivery_state, delivery_state_at)
        VALUES ($1, $2, 't', $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        job_id,
        repo,
        status,
        issue,
        cost,
        resumes,
        merged - created,
        merged - ran,
        merged,
        "merged" if status == "pr_merged" else "none",
        merged if status == "pr_merged" else None,
    )


def _by_achievement(rules, counted: dict[str, float]) -> dict[str, float]:
    return {
        a.id: counted.get("audit" if a.entry.metric in ("stale_claims", "clean_audit") else a.key, 0)
        for a in rules.achievements
    }


async def test_each_shipped_threshold_counts_just_inside_and_not_just_outside(db_with_migrations) -> None:
    """Rows a minute (a cent, one step) either side of every threshold an operator can now set."""
    db = db_with_migrations
    minute = timedelta(minutes=1)
    day = timedelta(days=1)
    await db.execute("INSERT INTO issues (id, title, repo) VALUES ('iss-a', 'a', 'o/r'), ('iss-b', 'b', 'o/r')")
    # night shift, hours 0 to 4: 03:59 counts, 04:00 does not
    await _job_at(db, "night-in", MONDAY + timedelta(hours=4) - minute)
    await _job_at(db, "night-out", MONDAY + timedelta(hours=4))
    # Friday night, from 20:00: 19:59 does not count, 20:00 does
    friday = MONDAY + 4 * day
    await _job_at(db, "friday-out", friday + timedelta(hours=20) - minute)
    await _job_at(db, "friday-in", friday + timedelta(hours=20))
    # the weekend, Saturday and Sunday: Sunday 23:59 counts, Monday 00:00 does not (it is a night shift)
    await _job_at(db, "weekend-sat", friday + day + timedelta(hours=12))
    await _job_at(db, "weekend-sun", friday + 3 * day - minute)
    await _job_at(db, "weekend-out", friday + 3 * day)
    # under one US dollar: 0.99 counts, 1.00 does not
    await _job_at(db, "penny-in", MONDAY + 8 * day + timedelta(hours=12), cost=0.99)
    await _job_at(db, "penny-out", MONDAY + 9 * day + timedelta(hours=12), cost=1.00)
    # within a day of its start: 23:59 counts, 24:00 does not
    await _job_at(db, "speed-in", MONDAY + 10 * day + timedelta(hours=12), created=day - minute)
    await _job_at(db, "speed-out", MONDAY + 11 * day + timedelta(hours=12), created=day)
    # ran more than two hours: 2:00 does not count, 2:01 does
    await _job_at(db, "grind-out", MONDAY + 21 * day + timedelta(hours=12), ran=timedelta(hours=2))
    await _job_at(db, "grind-in", MONDAY + 22 * day + timedelta(hours=12), ran=timedelta(hours=2) + minute)
    # three merges a day: three on one day count, two on another do not
    for i in range(3):
        await _job_at(db, f"streak-in-{i}", MONDAY + 14 * day + timedelta(hours=12 + i))
    for i in range(2):
        await _job_at(db, f"streak-out-{i}", MONDAY + 15 * day + timedelta(hours=12 + i))
    # 25 December: 00:00 counts (a night shift too), 24 December 23:59 does not
    christmas = datetime(2025, 12, 25, tzinfo=UTC)
    await _job_at(db, "holiday-in", christmas)
    await _job_at(db, "holiday-out", christmas - minute)
    # the third attempt: an issue merged on its second does not count, one on its third does
    await _job_at(db, "a-1", MONDAY + 16 * day, created=40 * day, issue="iss-a", status="failed")
    await _job_at(db, "a-2", MONDAY + 16 * day + timedelta(hours=12), issue="iss-a")
    for i in (1, 2):
        await _job_at(db, f"b-{i}", MONDAY + 17 * day, created=(40 + i) * day, issue="iss-b", status="failed")
    await _job_at(db, "b-3", MONDAY + 17 * day + timedelta(hours=12), issue="iss-b")
    # interrupted twice: one resume does not count, two do
    await _job_at(db, "resumed-once", MONDAY + 18 * day + timedelta(hours=12), status="completed", resumes=1)
    await _job_at(db, "resumed-twice", MONDAY + 19 * day + timedelta(hours=12), status="completed", resumes=2)
    # seen ten times and healed; seen 99 times and fixed
    for case, seen, verdict in (
        ("hc-9", 9, "healed"),
        ("hc-10", 10, "healed"),
        ("hc-98", 98, "pending"),
        ("hc-99", 99, "pending"),
    ):
        await db.execute(
            "INSERT INTO healing_cases (id, repo, origin, occurrence_count, healed_verdict, verified_at, merged_at) "
            "VALUES ($1, 'o/r', 'human', $2, $3, $4, $4)",
            case,
            seen,
            verdict,
            MONDAY + 20 * day,
        )
    # a design audit scored 90 or more: 89 does not count, 90 does
    for repo, score in (("o/x", 89), ("o/y", 90)):
        await db.execute(
            "INSERT INTO repo_standard_state (repo, owner, kind, score, last_audit_at) VALUES ($1, 'o', 'design', $2, $3)",
            repo,
            score,
            MONDAY + 21 * day,
        )

    rules = default_rules()
    got = _by_achievement(
        rules, _counts(await ProgressionRepository(db).facts(rules.fact_queries, rules.pack.timezone))
    )
    expected = {
        "night_shift": 3,  # 03:59 Monday, 00:00 Monday, 00:00 on 25 December
        "friday_night_merge": 1,
        "afk_weekend": 2,
        "merged_for_a_penny": 1,
        "speedrun_any_percent": 1,
        "long_grind": 1,
        "hot_streak": 1,
        "holiday_event": 1,
        "iron_will": 1,
        "lag_survivor": 1,
        "back_from_the_respawn": 2,
        "elite_hunter": 1,
        "hunt_complete": 2,
        "ninety_nine_problems": 1,
        "fashion_inspector": 1,
    }
    assert {k: got[k] for k in expected} == expected, "a threshold moved: each counts just inside and not just outside"


async def test_a_threshold_hour_falls_on_the_packs_clock_not_utcs(db_with_migrations) -> None:
    """Night shift ends at 04:00 local: 08:59 UTC is 03:59 in New York, 09:00 UTC is 04:00."""
    db = db_with_migrations
    await _job_at(db, "ny-in", MONDAY + timedelta(hours=9) - timedelta(minutes=1))
    await _job_at(db, "ny-out", MONDAY + timedelta(hours=9))
    rules = default_rules()
    repo = ProgressionRepository(db)
    in_utc = _by_achievement(rules, _counts(await repo.facts(rules.fact_queries, "UTC")))
    in_new_york = _by_achievement(rules, _counts(await repo.facts(rules.fact_queries, "America/New_York")))
    assert in_utc["night_shift"] == 0, "08:59 and 09:00 are mornings in UTC"
    assert in_new_york["night_shift"] == 1, "03:59 in New York is a night shift, 04:00 is not"


# ---- a job keeps what it earned when its pull request merges or closes (#210) ----


async def _pull_request_settles(db, job_id: str, state: str, at: datetime) -> None:
    """What merge sync writes when a job's pull request merges or closes: its delivery, and the status that follows."""

    def decide(row: dict) -> DeliveryWrite:
        return DeliveryWrite(delivery_state=state, delivery_state_at=at, status=aligned_status(row["status"], state))

    assert await JobDeliveryRepository(db).apply(job_id, decide) is not None, f"merge sync wrote nothing on {job_id}"


@pytest.mark.parametrize("state", ["merged", "closed"])
async def test_a_job_that_finished_its_work_keeps_its_outcomes_when_its_pull_request_settles(
    db_with_migrations, state: str
) -> None:
    """An audit, a QA run and a resumed job read the same before and after their status follows the pull request."""
    db = db_with_migrations
    await _job(db, "job-audit", status="completed", template="context-audit", report="Stale: 3\nFiles: 1\n")
    await _job(db, "job-qa", status="completed", template="qa")
    await _job(db, "job-resumed", status="completed")
    await db.execute("UPDATE jobs SET resume_attempts = 2 WHERE job_id = 'job-resumed'")
    await db.execute("UPDATE jobs SET pr_url = 'https://forge/o/r/pull/' || job_id, delivery_state = 'pr_open'")
    await _trace(db, "trc-resumed", "job-resumed", "developer")
    rules = default_rules()
    keys = {q.key for q in rules.fact_queries if q.fragment in ("audit", "qa_passed", "resumed")}
    repo = ProgressionRepository(db)

    async def outcomes() -> tuple[list, list, list]:
        events = [
            (e.source, e.agent_type, e.at, e.report)
            for e in await repo.events()
            if e.source in ("audited", "qa_passed", "resumed")
        ]
        facts = [
            (f.metric, f.at, f.value, f.report)
            for f in await repo.facts(rules.fact_queries, rules.pack.timezone)
            if f.metric in keys
        ]
        journal = sorted((j.template or "", j.report or "") for j in (await repo.journal()).jobs)
        return events, facts, journal

    before = await outcomes()
    assert sorted(e[0] for e in before[0]) == ["audited", "qa_passed", "resumed"], before[0]
    assert ("context-audit", "Stale: 3\nFiles: 1\n") in before[2], before[2]
    for job_id in ("job-audit", "job-qa", "job-resumed"):
        await _pull_request_settles(db, job_id, state, datetime.now(UTC) + timedelta(days=2))
    statuses = {r["status"] for r in await db.fetch("SELECT status FROM jobs")}
    assert statuses == {"pr_merged" if state == "merged" else "pr_closed"}, statuses
    after = await outcomes()
    assert after[0] == before[0], f"the pull request {state}: the events it earned went from {before[0]} to {after[0]}"
    assert after[1] == before[1], f"the pull request {state}: the facts it earned went from {before[1]} to {after[1]}"
    assert after[2] == before[2], f"the pull request {state}: the journal's report went from {before[2]} to {after[2]}"
