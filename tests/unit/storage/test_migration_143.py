"""Migration 143 — behaviour from agent and template rows, not built-in names (#167).

An upgraded database's rows must say what the code said by their names
before (tests/fixtures/name_lists_before_rows.py), and the boot seed must
never overwrite what an operator changed since.

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations
from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository
from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES
from tests.fixtures import name_lists_before_rows as before

#: This migration's number.
VERSION = 143

TEMPLATE_COLUMNS = ("fixed_route", "offered_to_triage", "job_kind", "delivers", "leader_agent")
AGENT_COLUMNS = ("station", "quest_template")


def test_the_behaviour_rows_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_142.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must add the template and agent behaviour columns"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


async def _columns(db: DatabasePool, table: str, names: tuple[str, ...]) -> list[str]:
    rows = await db.fetch(
        "SELECT column_name FROM information_schema.columns WHERE table_name = $1 AND column_name = ANY($2::text[])",
        table,
        list(names),
    )
    return sorted(str(r["column_name"]) for r in rows)


async def _undo(db: DatabasePool) -> None:
    """The database as it stood before the migration."""
    assert await _columns(db, "pipeline_templates", TEMPLATE_COLUMNS) == sorted(TEMPLATE_COLUMNS), (
        "pipeline_templates lacks a behaviour column after migrating"
    )
    assert await _columns(db, "agent_definitions", AGENT_COLUMNS) == sorted(AGENT_COLUMNS), (
        "agent_definitions lacks station or quest_template after migrating"
    )
    await db.execute("ALTER TABLE pipeline_templates " + ", ".join(f"DROP COLUMN {c}" for c in TEMPLATE_COLUMNS))
    await db.execute("ALTER TABLE agent_definitions DROP COLUMN station, DROP COLUMN quest_template")
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)


async def _old_template(
    db: DatabasePool, name: str, graph: dict[str, Any], *, builtin: bool, kind: str | None = None
) -> None:
    """A template row as a release before this migration wrote it."""
    await db.execute(
        "INSERT INTO pipeline_templates (id, name, graph_definition, is_builtin, default_for_kind) "
        "VALUES ($1, $1, $2::jsonb, $3, $4)",
        name,
        graph,  # the pool's jsonb codec encodes it
        builtin,
        kind,
    )


def _route(*agents: str) -> dict[str, Any]:
    nodes = [{"node_id": f"n{i}", "agent_type": a} for i, a in enumerate(("triage", *agents))]
    edges = [
        {"edge_id": f"e{i}", "source": f"n{i}", "target": f"n{i + 1}", "edge_type": "flow"} for i in range(len(agents))
    ]
    return {"nodes": nodes, "edges": edges}


async def _templates(db: DatabasePool) -> dict[str, dict[str, Any]]:
    rows = await db.fetch(f"SELECT name, {', '.join(TEMPLATE_COLUMNS)} FROM pipeline_templates ORDER BY name")
    return {str(r["name"]): {c: r[c] for c in TEMPLATE_COLUMNS} for r in rows}


async def _agents(db: DatabasePool) -> dict[str, dict[str, Any]]:
    rows = await db.fetch("SELECT type, station, quest_template FROM agent_definitions ORDER BY type")
    return {str(r["type"]): {"station": r["station"], "quest_template": r["quest_template"]} for r in rows}


def _what_the_name_said(name: str, graph: dict[str, Any], kind: str | None) -> dict[str, Any]:
    specialist = name in before.SPECIALIST_TEMPLATE_NAMES
    return {
        "fixed_route": specialist,
        "offered_to_triage": not specialist,
        # A kind default records its own kind; job_kind is what a template that is none records.
        "job_kind": None if kind else before.recorded_kind(name, graph, None),
        "delivers": before.TEMPLATE_DELIVERS.get(name, ""),
        "leader_agent": before.LEADER_BY_TEMPLATE.get(name),
    }


@pytest.mark.requires_postgres
async def test_an_upgrade_writes_each_template_what_its_name_said(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _undo(db)
    for name, seed in BUILTIN_PIPELINE_TEMPLATES.items():
        await _old_template(db, name, seed["graph_definition"], builtin=True, kind=seed.get("default_for_kind"))
    # The deployment's own: a copy of a specialist route, one of two specialists, a coding route and an
    # empty graph; none of them was offered anything by name.
    await _old_template(db, "my-why", BUILTIN_PIPELINE_TEMPLATES["why"]["graph_definition"], builtin=False)
    await _old_template(db, "two-specialists", _route("why", "how", "output"), builtin=False)
    await _old_template(db, "my-fix", _route("developer", "reviewer"), builtin=False)
    await _old_template(db, "specialist-then-code", _route("kintsugi", "developer"), builtin=False)
    await _old_template(db, "empty", {"nodes": [], "edges": []}, builtin=False)
    await run_migrations(db)

    after = await _templates(db)
    expected = {
        name: _what_the_name_said(name, seed["graph_definition"], seed.get("default_for_kind"))
        for name, seed in BUILTIN_PIPELINE_TEMPLATES.items()
    }
    for name, graph, kind in (
        ("my-why", BUILTIN_PIPELINE_TEMPLATES["why"]["graph_definition"], None),
        ("two-specialists", _route("why", "how", "output"), None),
        ("my-fix", _route("developer", "reviewer"), None),
        ("specialist-then-code", _route("kintsugi", "developer"), None),
        ("empty", {"nodes": [], "edges": []}, None),
    ):
        expected[name] = _what_the_name_said(name, graph, kind)
    differ = {name: (after.get(name), want) for name, want in expected.items() if after.get(name) != want}
    assert not differ, f"rows that say other than their names did (got, want): {differ}"
    assert expected["two-specialists"]["job_kind"] == "analysis", "a route of specialists only stays analysis"
    assert expected["my-why"]["fixed_route"] is False, "a copy of a specialist route was triaged before, by name"


@pytest.mark.requires_postgres
async def test_an_upgrade_writes_each_agent_what_its_name_said(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _undo(db)
    types = sorted({*before.STATION_OF, *before.QUEST_TEMPLATE_BY_AGENT, "triage", "my-own-agent"})
    for agent_type in types:
        await db.execute(
            "INSERT INTO agent_definitions (type, description, model) VALUES ($1, 'd', 'm') ON CONFLICT (type) DO NOTHING",
            agent_type,
        )
    # An agent of the deployment's own under a built-in's name kept what the name gave it.
    await db.execute("UPDATE agent_definitions SET is_builtin = false WHERE type = 'why'")
    await run_migrations(db)

    after = await _agents(db)
    for agent_type in types:
        offered = agent_type in before.QUEST_TEMPLATE_BY_AGENT
        want = {
            "station": before.STATION_OF.get(agent_type, "desk"),
            "quest_template": (before.QUEST_TEMPLATE_BY_AGENT.get(agent_type) or "") if offered else None,
        }
        assert after[agent_type] == want, f"{agent_type}: got {after[agent_type]}, want {want}"

    column = await db.fetchrow(
        "SELECT is_nullable, column_default FROM information_schema.columns "
        "WHERE table_name = 'agent_definitions' AND column_name = 'station'"
    )
    assert column is not None and column["is_nullable"] == "NO", "station is NOT NULL"
    assert column["column_default"] == "'desk'::text", f"a new agent sits at a desk: {column['column_default']}"


@pytest.mark.requires_postgres
async def test_the_boot_seed_keeps_what_an_operator_changed(db_with_migrations: DatabasePool) -> None:
    """Built-in templates are upserted at every boot: the behaviour columns are the operator's, like
    default_for_kind, so only a first insert takes the seed's."""
    db = db_with_migrations
    repo = PipelineTemplatesRepository(db)
    from embry0.storage.seeds.pipeline_templates_builtin import seed_builtin_pipeline_templates

    await seed_builtin_pipeline_templates(repo)
    seeded = await _templates(db)
    assert seeded["why"] == {
        "fixed_route": True,
        "offered_to_triage": False,
        "job_kind": "analysis",
        "delivers": "",
        "leader_agent": "why",
    }, seeded["why"]
    assert seeded["map-areas"]["delivers"] == before.TEMPLATE_DELIVERS["map-areas"]

    changed = {
        "fixed_route": False,
        "offered_to_triage": True,
        "job_kind": None,
        "delivers": "an answer to the question",
        "leader_agent": "how",
    }
    await db.execute(
        "UPDATE pipeline_templates SET fixed_route = $1, offered_to_triage = $2, job_kind = $3, delivers = $4, "
        "leader_agent = $5, graph_definition = '{}'::jsonb WHERE name = 'why'",
        *changed.values(),
    )
    await db.execute("DELETE FROM pipeline_templates WHERE name = 'how'")
    await seed_builtin_pipeline_templates(repo)

    after = await _templates(db)
    assert after["why"] == changed, f"the boot seed overwrote what the operator chose: {after['why']}"
    graph = await db.fetchval("SELECT graph_definition FROM pipeline_templates WHERE name = 'why'")
    assert graph == BUILTIN_PIPELINE_TEMPLATES["why"]["graph_definition"], "the seed still restores the graph"
    assert after["how"] == seeded["how"], "a built-in inserted again takes the seed's values"
