"""The behaviour columns (#167) in the database: written, read and copied by the repositories, and read by
the queries that credit a job's work and brief it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED, AgentDefinitionsRepository
from embry0.storage.repositories.job_briefs import JobBriefsRepository
from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository
from embry0.storage.repositories.progression import ProgressionRepository
from embry0.storage.repositories.world import WorldRepository
from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES
from embry0.workflows import row_behaviour as rb

pytestmark = pytest.mark.requires_postgres

GRAPH = {
    "nodes": [{"node_id": "t", "agent_type": "triage"}, {"node_id": "s", "agent_type": "slopmaster"}],
    "edges": [{"edge_id": "e1", "source": "t", "target": "s", "edge_type": "flow"}],
}
BEHAVIOUR = {
    "fixed_route": True,
    "offered_to_triage": False,
    "job_kind": "analysis",
    "delivers": "a list of dead flags",
    "leader_agent": "why",
}


async def test_a_template_keeps_what_it_says_of_its_jobs(db_with_migrations: DatabasePool) -> None:
    repo = PipelineTemplatesRepository(db_with_migrations)
    plain = await repo.create(name="plain", graph_definition=GRAPH)
    assert {k: plain[k] for k in rb.TEMPLATE_DEFAULTS} == dict(rb.TEMPLATE_DEFAULTS), plain

    made = await repo.create(name="scout-pass", graph_definition=GRAPH, **BEHAVIOUR)
    assert {k: made[k] for k in BEHAVIOUR} == BEHAVIOUR, made
    listed = {r["name"]: r for r in await repo.list_all()}
    assert {k: listed["scout-pass"][k] for k in BEHAVIOUR} == BEHAVIOUR, "the summary carries what the row says"

    updated = await repo.update(made["id"], job_kind=None, leader_agent=None, delivers="  a report  ")
    assert (updated["job_kind"], updated["leader_agent"], updated["delivers"]) == (None, None, "a report")

    copy = await repo.duplicate(made["id"], new_name="scout-pass-2")
    assert {k: copy[k] for k in BEHAVIOUR} == {k: updated[k] for k in BEHAVIOUR}, (
        "a copy treats its jobs as its source does"
    )
    assert copy["default_for_kind"] is None, "a copy is no kind's default"

    with pytest.raises(ValueError, match="job_kind"):
        await repo.update(made["id"], job_kind="chores")
    with pytest.raises(ValueError, match="delivers"):
        await repo.create(name="bad", graph_definition=GRAPH, delivers="two\nlines")
    with pytest.raises(ValueError, match="Unknown template fields"):
        await repo.create(name="bad", graph_definition=GRAPH, roles=["coder"])


async def test_an_agent_keeps_its_station_and_its_task(db_with_migrations: DatabasePool) -> None:
    repo = AgentDefinitionsRepository(db_with_migrations)
    made = await repo.create("scout", "d", "m", tools=["Read"])
    assert (made["station"], made["quest_template"]) == ("desk", None), made
    made = await repo.update("scout", station="lectern", quest_template="scout-pass")
    assert (made["station"], made["quest_template"]) == ("lectern", "scout-pass"), made
    made = await repo.update("scout", quest_template="")
    assert made["quest_template"] == "", "'' is a task naming no template, not none"
    with pytest.raises(ValueError, match="station"):
        await repo.update("scout", station="throne")
    with pytest.raises(ValueError, match="station"):
        await repo.create("other", "d", "m", tools=["Read"], station="throne")


async def test_every_seeded_builtin_agent_holds_its_station_and_task(db_with_migrations: DatabasePool) -> None:
    """The qa, onboarding and pr-reviewer rows come from their own seeders; each writes its seed's values."""
    from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents
    from embry0.workflows.onboard.agent_seed import seed_onboarding_agent
    from embry0.workflows.pr_review.agent_seed import seed_pr_reviewer_agent
    from embry0.workflows.qa.agent_seed import seed_qa_agent

    db = db_with_migrations
    await db.execute("DELETE FROM agent_definitions")
    repo = AgentDefinitionsRepository(db)
    for seed in (seed_qa_agent, seed_onboarding_agent, seed_pr_reviewer_agent, seed_missing_builtin_agents):
        await seed(repo)
    rows = {r["type"]: r for r in await repo.list_all()}
    differ = {
        t: ((rows[t]["station"], rows[t]["quest_template"]), (seed["station"], seed["quest_template"]))
        for t, seed in BUILTIN_SEED.items()
        if (rows[t]["station"], rows[t]["quest_template"]) != (seed["station"], seed["quest_template"])
    }
    assert not differ, f"seeded rows that differ from their seed (got, want): {differ}"


async def test_a_reset_puts_the_station_and_the_task_back(db_with_migrations: DatabasePool) -> None:
    from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents

    repo = AgentDefinitionsRepository(db_with_migrations)
    await seed_missing_builtin_agents(repo)
    await repo.update("developer", station="desk", quest_template=None)
    reset = await repo.reset("developer")
    assert (reset["station"], reset["quest_template"]) == ("anvil", ""), reset


# --- what reads the rows --------------------------------------------------------------------------------------

AT = datetime(2026, 3, 2, 12, tzinfo=UTC)


async def _job(db: DatabasePool, job_id: str, template: str | None, **row: Any) -> None:
    row = {"status": "pr_merged", "pr_url": f"https://forge/pull/{job_id}", "delivery_state": "merged", **row}
    await db.execute(
        "INSERT INTO jobs (job_id, repo, area, task, status, pipeline_template, pr_url, delivery_state, "
        "delivery_state_at, finished_at, created_at, budget_overrun_usd, job_kind) "
        "VALUES ($1, 'acme/widgets', 'Billing', 'Clean the flags', $2, $3, $4, $5, $6, $6, $7, 0, $8)",
        job_id,
        row["status"],
        template,
        row["pr_url"],
        row["delivery_state"],
        row.get("finished_at", AT),
        AT - timedelta(hours=1),
        row.get("job_kind", "code"),
    )


async def _seed(db: DatabasePool) -> None:
    """The built-in templates and agents, as the boot seeders write them."""
    from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents
    from embry0.storage.seeds.pipeline_templates_builtin import seed_builtin_pipeline_templates

    await seed_builtin_pipeline_templates(PipelineTemplatesRepository(db))
    await seed_missing_builtin_agents(AgentDefinitionsRepository(db))


async def _plan(db: DatabasePool, job_id: str, template: str, kind: str = "code") -> dict[str, Any]:
    """Plan ``job_id``, which names ``template``, as the job's plan_route step does."""
    from unittest.mock import patch

    from embry0.storage.repositories.jobs import JobsRepository
    from embry0.workflows.issue_to_pr import plan_route

    config = {
        "configurable": {
            "pipeline_templates_repo": PipelineTemplatesRepository(db),
            "jobs_repo": JobsRepository(db),
            "db": db,
        }
    }
    state = {
        "job_id": job_id,
        "task": "Clean the flags",
        "pipeline_template_request": template,
        "triage_decision": {"job_kind": kind, "pipeline_template": template},
    }
    with patch.object(plan_route, "get_stream_writer", return_value=lambda _e: None):
        return await plan_route.plan_route_node(state, config)


def _credit(events: list[Any]) -> list[tuple[str, str | None]]:
    return sorted((e.source, e.agent_type) for e in events if e.source in ("merged", "opened", "cleaned", "audited"))


async def test_a_job_keeps_the_credit_it_recorded_whatever_its_template_row_says_later(
    db_with_migrations: DatabasePool,
) -> None:
    """Credit is what plan_route recorded on the job (#167): an edit or a deletion of the template row after
    changes no past credit, and a new job is credited as the row says by then."""
    db = db_with_migrations
    await _seed(db)
    repo = PipelineTemplatesRepository(db)
    slop_graph = BUILTIN_PIPELINE_TEMPLATES["slop-cleanup"]["graph_definition"]
    await repo.create(name="my-slop", graph_definition=slop_graph, leader_agent="slopmaster")
    planned = {"j-slop": "slop-cleanup", "j-my-slop": "my-slop", "j-fix": "quick-fix", "j-audit": "context-audit"}
    for job_id, template in planned.items():
        await _job(db, job_id, template, status="running", pr_url=None, delivery_state="none")
        await _plan(db, job_id, template, kind="analysis" if template == "context-audit" else "code")
    recorded = {
        r["job_id"]: (r["coding_agent"], r["leader_agent"])
        for r in await db.fetch("SELECT job_id, coding_agent, leader_agent FROM jobs")
    }
    assert recorded == {
        "j-slop": ("slopmaster", "slopmaster"),
        "j-my-slop": ("slopmaster", "slopmaster"),
        "j-fix": ("developer", None),
        "j-audit": ("developer", "context-auditor"),
    }, f"what plan_route records: {recorded}"
    await db.execute(
        "UPDATE jobs SET status = 'pr_merged', delivery_state = 'merged', pr_url = 'https://forge/pull/' || job_id "
        "WHERE job_id <> 'j-audit'"
    )
    await db.execute("UPDATE jobs SET status = 'completed', result_summary = 'Stale: 1' WHERE job_id = 'j-audit'")
    before_edit = _credit(await ProgressionRepository(db).events())
    cleaned = await WorldRepository(db).cleaned(days=100_000)
    assert ("audited", "context-auditor") in before_edit and before_edit.count(("cleaned", "slopmaster")) == 2
    assert cleaned == {("acme/widgets", "Billing"): 2}, cleaned

    # The operator clears the audit's leader, gives the slop route to the developer and deletes their own.
    fix_graph = BUILTIN_PIPELINE_TEMPLATES["quick-fix"]["graph_definition"]
    await db.execute("UPDATE pipeline_templates SET leader_agent = NULL WHERE name = 'context-audit'")
    await db.execute(
        "UPDATE pipeline_templates SET leader_agent = 'developer', graph_definition = $1 WHERE name = 'slop-cleanup'",
        fix_graph,
    )
    await db.execute("DELETE FROM pipeline_templates WHERE name = 'my-slop'")
    assert _credit(await ProgressionRepository(db).events()) == before_edit, "an edit of the rows moved past credit"
    assert await WorldRepository(db).cleaned(days=100_000) == cleaned, "an edit of the rows moved the dungeon"

    # A job planned since is credited as the rows say now.
    await _job(db, "j-after", "slop-cleanup", status="running", pr_url=None, delivery_state="none")
    await _plan(db, "j-after", "slop-cleanup")
    after = await db.fetchrow("SELECT coding_agent, leader_agent FROM jobs WHERE job_id = 'j-after'")
    assert (after["coding_agent"], after["leader_agent"]) == ("developer", "developer"), dict(after)


async def test_an_audit_is_a_job_the_context_auditor_led(db_with_migrations: DatabasePool) -> None:
    """A copy of the context-audit template is an audit (it keeps the leader); a template led by another agent
    is not, whatever its name (#167)."""
    db = db_with_migrations
    await _seed(db)
    repo = PipelineTemplatesRepository(db)
    audit = next(r for r in await repo.list_all() if r["name"] == "context-audit")
    await repo.duplicate(audit["id"], new_name="my-audit")
    other = await repo.create(
        name="audit-led-by-why", graph_definition=BUILTIN_PIPELINE_TEMPLATES["why"]["graph_definition"]
    )
    await repo.update(other["id"], leader_agent="why")
    for job_id, template in (("j-copy", "my-audit"), ("j-why", "audit-led-by-why"), ("j-builtin", "context-audit")):
        await _job(db, job_id, template, status="running", pr_url=None, delivery_state="none")
        await _plan(db, job_id, template, kind="analysis")
    await db.execute("UPDATE jobs SET status = 'completed', result_summary = 'Stale: 2'")
    audited = sorted(
        (e.agent_type, e.report) for e in await ProgressionRepository(db).events() if e.source == "audited"
    )
    assert audited == [("context-auditor", "Stale: 2"), ("context-auditor", "Stale: 2")], (
        f"the copy and the built-in are audits, the template led by why is none: {audited}"
    )
    journal = await ProgressionRepository(db).journal()
    assert sorted(j.template for j in journal.jobs if j.is_audit) == ["context-audit", "my-audit"]


async def test_a_brief_says_what_the_jobs_template_row_delivers(db_with_migrations: DatabasePool) -> None:
    from embry0.services.job_briefs import input_hash, user_prompt

    db = db_with_migrations
    await _seed(db)
    await PipelineTemplatesRepository(db).create(name="scout-pass", graph_definition=GRAPH, **BEHAVIOUR)
    for job_id, template in (("j-own", "scout-pass"), ("j-map", "map-areas"), ("j-qa", "qa"), ("j-none", None)):
        await _job(db, job_id, template, status="running", pr_url=None, delivery_state="none", finished_at=None)
    candidates = {c["job_id"]: c for c in await JobBriefsRepository(db).candidates()}
    said = {
        job_id: user_prompt(c).split("What it delivers: ", 1)[1].split("\n", 1)[0] for job_id, c in candidates.items()
    }
    assert said == {
        "j-own": "a list of dead flags",
        "j-map": "a pull request that maps the repository's areas",
        "j-qa": "a test report",
        "j-none": "a pull request with the change",
    }, said
    stripped = {k: v for k, v in candidates["j-own"].items() if k != "template_delivers"}
    assert input_hash(candidates["j-own"]) == input_hash(stripped), "what a template delivers is no input of the hash"


async def test_a_rename_carries_each_agents_guild_task_with_it(db_with_migrations: DatabasePool) -> None:
    """An agent's quest_template names a template: renaming the template moves it in the same transaction, so
    the guild keeps sending the agent on what the operator chose (#167)."""
    db = db_with_migrations
    templates = PipelineTemplatesRepository(db)
    agents = AgentDefinitionsRepository(db)
    made = await templates.create(name="scout-pass", graph_definition=GRAPH)
    await agents.create("scout", "d", "m", tools=["Read"], quest_template="scout-pass")
    await agents.create("watcher", "d", "m", tools=["Read"], quest_template="other")
    await templates.update(made["id"], name="scout-run")
    tasks = {r["type"]: r["quest_template"] for r in await agents.list_all() if r["type"] in ("scout", "watcher")}
    assert tasks == {"scout": "scout-run", "watcher": "other"}, f"the task did not follow the rename: {tasks}"
    await templates.update(made["id"], description="same name")
    assert (await agents.get("scout"))["quest_template"] == "scout-run", "a save that keeps the name moves nothing"


async def _audit_job(
    db: DatabasePool, job_id: str, repo: str, template: str, leader: str, by: str | None = None
) -> None:
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, leader_agent, dispatched_by, result_summary, "
        "finished_at, created_at, budget_overrun_usd) "
        "VALUES ($1, $2, 't', 'completed', $3, $4, $5, 'Stale: 3', $6, $7, 0)",
        job_id,
        repo,
        template,
        leader,
        by,
        AT,
        AT - timedelta(hours=1),
    )


async def test_progression_the_findings_feed_and_the_scheduler_agree_on_what_an_audit_is(
    db_with_migrations: DatabasePool,
) -> None:
    """One predicate (audit_jobs): a copy of the context-audit template, led by the context auditor, is an audit
    to all three; a context-audit job led by another agent, after an operator changed the template's leader, earns
    no audit credit and offers no findings. The scheduler counts every audit, and also its own template's jobs
    and its own dispatches whatever led them, so it never starts an audit again on every tick."""
    from embry0.services.context_audit import ACTOR, FIX_ACTOR, ContextAuditLoop
    from embry0.storage.repositories.jobs import JobsRepository
    from embry0.storage.repositories.proposals import ProposalsRepository

    db = db_with_migrations
    await _audit_job(db, "j-copy", "acme/copy", "my-audit", "context-auditor")
    await _audit_job(db, "j-changed", "acme/changed", "context-audit", "interrogator")
    await _audit_job(db, "j-scheduled", "acme/scheduled", "context-audit", "interrogator", by=ACTOR)
    await _audit_job(db, "j-fix", "acme/fix", "context-audit", "context-auditor", by=FIX_ACTOR)

    journal = await ProgressionRepository(db).journal()
    progression = sorted(j.repo for j in journal.jobs if j.is_audit)
    audited = [e.agent_type for e in await ProgressionRepository(db).events() if e.source == "audited"]
    offered = sorted(r["repo"] for r in await ProposalsRepository(db).audits())
    loop = ContextAuditLoop.__new__(ContextAuditLoop)  # only _audits is read; it needs the jobs repository alone
    loop._jobs = JobsRepository(db)  # noqa: SLF001
    newest, live = await loop._audits(["acme/copy", "acme/changed", "acme/scheduled", "acme/fix"])  # noqa: SLF001

    assert progression == ["acme/copy"], f"progression's audits: {progression}"
    assert audited == ["context-auditor"], f"one audit earns its credit: {audited}"
    assert offered == progression, f"the findings feed offers what progression counts as audits: {offered}"
    assert sorted(newest) == ["acme/changed", "acme/copy", "acme/scheduled"], (
        f"the scheduler's newest: every audit, the copy included, and its own template's and dispatches' jobs, "
        f"never a fix job: {sorted(newest)}"
    )
    assert live == set(), live


async def test_a_job_that_names_a_template_takes_its_leader_when_it_is_created(
    db_with_migrations: DatabasePool,
) -> None:
    """Before plan_route records it: a pending audit is known for one (the scheduler's live check reads it)."""
    from embry0.storage.repositories.jobs import JobsRepository

    db = db_with_migrations
    await PipelineTemplatesRepository(db).create(
        name="my-audit", graph_definition=GRAPH, leader_agent="context-auditor"
    )
    jobs = JobsRepository(db)
    named = await jobs.create(repo="acme/widgets", task="t", pipeline_template="my-audit")
    plain = await jobs.create(repo="acme/widgets", task="t")
    unknown = await jobs.create(repo="acme/widgets", task="t", pipeline_template="qa")
    leaders = {r["job_id"]: r["leader_agent"] for r in await db.fetch("SELECT job_id, leader_agent FROM jobs")}
    assert leaders == {named: "context-auditor", plain: None, unknown: None}, leaders


async def test_two_renames_at_once_carry_the_task_to_the_last_name(db_with_migrations: DatabasePool) -> None:
    """The rename reads the old name under the row's lock: a rename beside it waits, then reads the name it left,
    so an agent's task follows both renames to the template's final name."""
    import asyncio

    import asyncpg

    db = db_with_migrations
    templates = PipelineTemplatesRepository(db)
    made = await templates.create(name="alpha", graph_definition=GRAPH)
    await AgentDefinitionsRepository(db).create("scout", "d", "m", tools=["Read"], quest_template="alpha")
    # Two of the pool's connections open, one for each rename, before the row is held.
    await asyncio.gather(*(db.fetchval("SELECT pg_sleep(0.05)") for _ in range(3)))
    blocker = await asyncpg.connect(db._url)  # noqa: SLF001 - a second session, holding the row
    try:
        held = blocker.transaction()
        await held.start()
        await blocker.execute("SELECT 1 FROM pipeline_templates WHERE id = $1 FOR UPDATE", made["id"])
        first = asyncio.create_task(templates.update(made["id"], name="beta"))
        second = asyncio.create_task(templates.update(made["id"], name="gamma"))
        for _ in range(200):  # until both renames wait on the row
            waiting = await blocker.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if waiting >= 2:
                break
            await asyncio.sleep(0.02)
        activity = await blocker.fetch(
            "SELECT state, wait_event_type, wait_event, left(query, 60) AS q FROM pg_stat_activity "
            "WHERE datname = current_database()"
        )
        assert waiting >= 2, f"both renames should wait on the row before it is let go: {[dict(a) for a in activity]}"
        await held.rollback()
    finally:
        await blocker.close()
    await asyncio.gather(first, second)
    name = await db.fetchval("SELECT name FROM pipeline_templates WHERE id = $1", made["id"])
    task = (await AgentDefinitionsRepository(db).get("scout"))["quest_template"]
    assert task == name, f"template now {name}, the agent's task {task}: a rename read a stale name"
