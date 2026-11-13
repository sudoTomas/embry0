"""Migration 145 — a repository's areas stored in embry0, its map's proposals and the one-time import (spec 2026-11-10).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES

#: This migration's number.
VERSION = 145

SEEDS = Path(__file__).parents[2] / "fixtures" / "seeds"
#: The area-mapper's prompt and description as the release before this migration seeded them.
OLD_PROMPT = (SEEDS / "area_mapper_prompt_before_145.txt").read_text()
OLD_DESCRIPTION = (SEEDS / "area_mapper_description_before_145.txt").read_text()
OLD_DELIVERS = "a pull request that maps the repository's areas"
OLD_SURFACES = "('qa_config', 'preferences')"
OLD_SOURCES = "('person', 'bootstrap', 'onboard', 'proposal', 'restore')"


def _sql() -> str:
    return next(sql for version, _, sql in MIGRATIONS if version == VERSION)


def test_the_areas_migration_is_listed_once() -> None:
    """ "Is the last entry" passed to tests/unit/storage/test_migration_146.py."""
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


def test_the_migration_matches_the_seed_it_replaces_and_writes_this_release_s() -> None:
    """A migration is history: its literals are pinned. It matches the area-mapper's text the release before
    seeded (by md5 of the frozen copy in tests/fixtures/seeds) and writes the seed of this release. When the seed
    changes again, freeze this release's text next to the old one and compare with that."""
    sql = _sql()
    assert hashlib.md5(OLD_PROMPT.encode()).hexdigest() in sql, "the old prompt's md5 is what the migration matches"
    assert hashlib.md5(OLD_DESCRIPTION.encode()).hexdigest() in sql, "and the old description's"
    seed = BUILTIN_SEED["area-mapper"]
    assert f"$seed${seed['system_prompt']}$seed$" in sql, "the migration writes this release's prompt"
    assert f"$seed${seed['description']}$seed$" in sql, "and this release's description"
    delivers = BUILTIN_PIPELINE_TEMPLATES["map-areas"]["delivers"]
    assert f"SET delivers = '{delivers.replace(chr(39), chr(39) * 2)}'" in sql, "and the template's delivers"


async def _undo(db: DatabasePool) -> None:
    """The database as it stood before the migration."""
    assert await db.fetchval("SELECT to_regclass('repo_areas')") is not None, "migrating made no repo_areas table"
    await db.execute("DROP TABLE repo_areas, repo_areas_proposals, repo_areas_imports")
    await db.execute("DROP SEQUENCE repo_areas_generation")
    await db.execute(
        "ALTER TABLE repo_config_revisions DROP CONSTRAINT repo_config_revisions_surface_check, "
        f"ADD CONSTRAINT repo_config_revisions_surface_check CHECK (surface IN {OLD_SURFACES}), "
        "DROP CONSTRAINT repo_config_revisions_source_check, "
        f"ADD CONSTRAINT repo_config_revisions_source_check CHECK (source IN {OLD_SOURCES})"
    )
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)


async def _area_mapper(db: DatabasePool, *, prompt: str, description: str, builtin: bool = True) -> None:
    await db.execute(
        "INSERT INTO agent_definitions (type, description, model, system_prompt, is_builtin) "
        "VALUES ('area-mapper', $1, 'claude-sonnet-5', $2, $3)",
        description,
        prompt,
        builtin,
    )


async def _map_template(db: DatabasePool, delivers: str) -> None:
    await db.execute(
        "INSERT INTO pipeline_templates (id, name, graph_definition, is_builtin, delivers) "
        "VALUES ('t-map', 'map-areas', '{}'::jsonb, true, $1)",
        delivers,
    )


async def _agent(db: DatabasePool) -> dict[str, Any]:
    row = await db.fetchrow("SELECT description, system_prompt FROM agent_definitions WHERE type = 'area-mapper'")
    assert row is not None
    return dict(row)


@pytest.mark.requires_postgres
async def test_an_upgrade_lists_the_repositories_registered_then_for_the_import(
    db_with_migrations: DatabasePool,
) -> None:
    """The import reads exactly the repositories registered when the migration ran, once: each is listed
    pending, and one registered afterwards is not listed at all."""
    db = db_with_migrations
    await _undo(db)
    for repo in ("acme/widgets", "acme/gadgets"):
        await db.execute("INSERT INTO repos (repo, forge) VALUES ($1, 'github')", repo)
    await run_migrations(db)
    await db.execute("INSERT INTO repos (repo, forge) VALUES ('acme/later', 'github')")

    rows = await db.fetch("SELECT repo, outcome, attempts FROM repo_areas_imports ORDER BY repo")
    assert [(r["repo"], r["outcome"], r["attempts"]) for r in rows] == [
        ("acme/gadgets", "pending", 0),
        ("acme/widgets", "pending", 0),
    ], "the ledger lists the repositories registered when the migration ran, and only those"


@pytest.mark.requires_postgres
async def test_the_built_in_rows_still_at_the_old_seed_take_this_release_s(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _undo(db)
    await _area_mapper(db, prompt=OLD_PROMPT, description=OLD_DESCRIPTION)
    await _map_template(db, OLD_DELIVERS)
    await run_migrations(db)

    seed = BUILTIN_SEED["area-mapper"]
    assert await _agent(db) == {"description": seed["description"], "system_prompt": seed["system_prompt"]}, (
        "an area-mapper still at the old seed takes this release's prompt and description"
    )
    delivers = await db.fetchval("SELECT delivers FROM pipeline_templates WHERE name = 'map-areas'")
    assert delivers == BUILTIN_PIPELINE_TEMPLATES["map-areas"]["delivers"], delivers


@pytest.mark.requires_postgres
async def test_an_operator_s_own_text_is_kept(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _undo(db)
    await _area_mapper(db, prompt=OLD_PROMPT + "\nAlso map the docs.", description="Our own words.")
    await _map_template(db, "our own delivery")
    await run_migrations(db)

    assert await _agent(db) == {"description": "Our own words.", "system_prompt": OLD_PROMPT + "\nAlso map the docs."}
    assert await db.fetchval("SELECT delivers FROM pipeline_templates WHERE name = 'map-areas'") == "our own delivery"


@pytest.mark.requires_postgres
async def test_a_deployment_s_own_agent_under_the_name_is_kept(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _undo(db)
    await _area_mapper(db, prompt=OLD_PROMPT, description=OLD_DESCRIPTION, builtin=False)
    await run_migrations(db)
    assert await _agent(db) == {"description": OLD_DESCRIPTION, "system_prompt": OLD_PROMPT}, (
        "an agent of the deployment's own is never rewritten, whatever its text"
    )


@pytest.mark.requires_postgres
async def test_history_takes_the_areas_surface_and_its_sources_and_refuses_others(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations

    async def revision(surface: str, source: str) -> None:
        await db.execute(
            "INSERT INTO repo_config_revisions (repo, surface, source) VALUES ('acme/widgets', $1, $2)", surface, source
        )

    for source in ("person", "proposal", "restore", "import", "file"):
        await revision("areas", source)
    await revision("qa_config", "onboard")
    with pytest.raises(asyncpg.CheckViolationError):
        await revision("settings", "person")
    with pytest.raises(asyncpg.CheckViolationError):
        await revision("areas", "upgrade")


@pytest.mark.requires_postgres
async def test_one_map_runs_or_waits_per_repository(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute("INSERT INTO repos (repo, forge) VALUES ('acme/widgets', 'github')")

    async def proposal(proposal_id: str, status: str) -> None:
        await db.execute(
            "INSERT INTO repo_areas_proposals (id, repo, status) VALUES ($1, 'acme/widgets', $2)", proposal_id, status
        )

    await proposal("a1", "applied")
    await proposal("a2", "rejected")
    await proposal("a3", "running")
    with pytest.raises(asyncpg.UniqueViolationError):
        await proposal("a4", "pending")
    with pytest.raises(asyncpg.CheckViolationError):
        await proposal("a5", "merged")


@pytest.mark.requires_postgres
async def test_a_document_lives_and_goes_with_its_repository(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute("INSERT INTO repos (repo, forge) VALUES ('acme/widgets', 'github')")
    await db.execute(
        "INSERT INTO repo_areas (repo, yaml_text, data) VALUES ('acme/widgets', 'areas: []', '{\"areas\": []}')"
    )
    row = await db.fetchrow("SELECT generation, schema_version FROM repo_areas WHERE repo = 'acme/widgets'")
    assert dict(row) == {"generation": 1, "schema_version": 1}, "a first write is generation 1 of schema 1"
    with pytest.raises(asyncpg.CheckViolationError):
        await db.execute("UPDATE repo_areas SET data = '[]' WHERE repo = 'acme/widgets'")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute("INSERT INTO repo_areas (repo, yaml_text, data) VALUES ('acme/nobody', '', '{}')")
    await db.execute("DELETE FROM repos WHERE repo = 'acme/widgets'")
    assert await db.fetchval("SELECT count(*) FROM repo_areas") == 0, "unregistering a repository removes its areas"


@pytest.mark.requires_postgres
async def test_a_repository_s_maps_go_with_it_and_no_map_names_one_never_registered(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    await db.execute("INSERT INTO repos (repo, forge) VALUES ('acme/widgets', 'github')")
    await db.execute("INSERT INTO repo_areas_proposals (id, repo, status) VALUES ('m1', 'acme/widgets', 'pending')")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute("INSERT INTO repo_areas_proposals (id, repo, status) VALUES ('m2', 'acme/nobody', 'pending')")
    await db.execute("DELETE FROM repos WHERE repo = 'acme/widgets'")
    assert await db.fetchval("SELECT count(*) FROM repo_areas_proposals") == 0, (
        "a repository removed from the registry takes its maps with it"
    )


@pytest.mark.requires_postgres
async def test_no_two_writes_share_a_generation(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    for repo in ("acme/widgets", "acme/gadgets"):
        await db.execute("INSERT INTO repos (repo, forge) VALUES ($1, 'github')", repo)
        await db.execute("INSERT INTO repo_areas (repo, yaml_text, data) VALUES ($1, '', '{}')", repo)
    generations = [r["generation"] for r in await db.fetch("SELECT generation FROM repo_areas ORDER BY generation")]
    assert len(set(generations)) == 2 and min(generations) >= 1, generations
