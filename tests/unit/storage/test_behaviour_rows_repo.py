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
from embry0.storage.repositories.progression import ProgressionRepository, coder_sql
from embry0.storage.repositories.world import WorldRepository
from embry0.workflows import row_behaviour as rb
from tests.fixtures.builtin_rows import seed_builtin_templates

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


async def test_a_jobs_pull_request_is_credited_as_its_template_row_says(db_with_migrations: DatabasePool) -> None:
    """The SQL rule (progression.coder_sql) and the Python one (row_behaviour.coder) agree for every job."""
    db = db_with_migrations
    await seed_builtin_templates(db)
    repo = PipelineTemplatesRepository(db)
    # A slop template of the deployment's own, led by the Slopmaster; one led by an agent that does not code.
    await repo.create(name="my-slop", graph_definition=GRAPH, leader_agent="slopmaster")
    await repo.create(name="my-report", graph_definition=GRAPH, leader_agent="why")
    jobs = {
        "j-slop": "slop-cleanup",
        "j-my-slop": "my-slop",
        "j-report": "my-report",
        "j-fix": "quick-fix",
        "j-map": "map-areas",
        "j-qa": "qa",
        "j-none": None,
        "j-gone": "renamed-since",
    }
    for job_id, template in jobs.items():
        await _job(db, job_id, template)

    events = await ProgressionRepository(db).events()
    rows = rb.pipelines_by_name(await repo.list_all())
    credited = {
        r["job_id"]: r["agent_type"]
        for r in await db.fetch(
            f"SELECT j.job_id, {coder_sql('jt')} AS agent_type "
            "FROM jobs j LEFT JOIN pipeline_templates jt ON jt.name = j.pipeline_template"
        )
    }
    want = {job_id: rb.coder(rows.get(template or "")) for job_id, template in jobs.items()}
    assert credited == want, f"the SQL and the Python credit differ: {credited} != {want}"
    assert (want["j-slop"], want["j-my-slop"], want["j-report"]) == ("slopmaster", "slopmaster", "developer")
    merged = sorted(str(e.agent_type) for e in events if e.source == "merged")
    assert merged == sorted(want.values()), f"each merged job is credited to its coder: {merged}"

    cleaned = sorted(e.agent_type for e in events if e.source == "cleaned")
    assert cleaned == ["slopmaster", "slopmaster"], f"each job the Slopmaster coded is a cleanup: {cleaned}"
    by_area = await WorldRepository(db).cleaned(days=100_000)
    assert by_area == {("acme/widgets", "Billing"): 2}, f"the dungeon counts both cleanups: {by_area}"


async def test_an_audit_is_credited_to_its_templates_leader(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await seed_builtin_templates(db)
    await _job(db, "j-audit", "context-audit", status="completed", pr_url=None, delivery_state="none")
    await db.execute("UPDATE jobs SET result_summary = 'Stale: 1' WHERE job_id = 'j-audit'")
    audited = [e.agent_type for e in await ProgressionRepository(db).events() if e.source == "audited"]
    assert audited == ["context-auditor"], audited
    await db.execute("UPDATE pipeline_templates SET leader_agent = 'why' WHERE name = 'context-audit'")
    audited = [e.agent_type for e in await ProgressionRepository(db).events() if e.source == "audited"]
    assert audited == ["why"], f"the row's leader, not a name in the query: {audited}"


async def test_a_brief_says_what_the_jobs_template_row_delivers(db_with_migrations: DatabasePool) -> None:
    from embry0.services.job_briefs import input_hash, user_prompt

    db = db_with_migrations
    await seed_builtin_templates(db)
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
