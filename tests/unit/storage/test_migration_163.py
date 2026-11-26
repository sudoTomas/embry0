"""Migration 163 — an agent's roles on its row (#395): ``coder`` runs it on the developer node, ``docs`` lets it put
files on the job's branch.

The developer node ran the agents a list of two names in the code named. An upgraded database's rows must say the
same: the two built-in coding agents hold ``coder``; triage, the reviewer and the context auditor hold ``docs``, as
the seed gives them; every other row none, an operator's own included. The number is ``VERSION`` alone: renumbering
the migration changes that line and this file's name.
"""

from __future__ import annotations

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS, run_migrations
from tests.fixtures import name_lists_before_rows as before

#: This migration's number.
VERSION = 163

pytestmark = [pytest.mark.requires_postgres]


def test_the_agent_roles_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_162.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must add agent_definitions.roles"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


async def _undo(db: DatabasePool) -> None:
    """The database as it stood before the migration."""
    assert await db.fetchval(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'agent_definitions' AND column_name = 'roles'"
    ), "agent_definitions lacks roles after migrating"
    await db.execute("ALTER TABLE agent_definitions DROP COLUMN roles")
    await db.execute("DELETE FROM embry0_migrations WHERE version = $1", VERSION)


async def _agent(db: DatabasePool, agent_type: str, *, builtin: bool) -> None:
    await db.execute(
        "INSERT INTO agent_definitions (type, description, model, is_builtin) VALUES ($1, 'd', 'm', $2) "
        "ON CONFLICT (type) DO NOTHING",
        agent_type,
        builtin,
    )


async def test_an_upgrade_gives_the_coder_role_to_the_agents_the_list_named(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _undo(db)
    for agent_type in ("developer", "slopmaster", "triage", "review", "context-auditor", "qa", "kintsugi"):
        await _agent(db, agent_type, builtin=True)
    await _agent(db, "scribe", builtin=False)  # an operator's own coding agent: it held no role before either
    await run_migrations(db)

    roles = {str(r["type"]): list(r["roles"]) for r in await db.fetch("SELECT type, roles FROM agent_definitions")}
    coders = {name for name, held in roles.items() if held == ["coder"]}
    assert coders == set(before.CODING_AGENTS), f"the rows holding coder are not the ones the list named: {roles}"
    assert {name for name, held in roles.items() if held == ["docs"]} == {"triage", "review", "context-auditor"}, roles
    assert all(held in ([], ["coder"], ["docs"]) for held in roles.values()), roles
    assert roles["scribe"] == [], "an operator's own agent is given no role: the operator gives it one"


async def test_a_role_the_check_does_not_name_is_refused(db_with_migrations: DatabasePool) -> None:
    await _agent(db_with_migrations, "scribe", builtin=False)
    for role in ("reviewer", "Coder", "Docs", ""):
        with pytest.raises(asyncpg.CheckViolationError):
            await db_with_migrations.execute(
                "UPDATE agent_definitions SET roles = ARRAY[$1::text] WHERE type = 'scribe'", role
            )
    await db_with_migrations.execute(
        "UPDATE agent_definitions SET roles = ARRAY['coder', 'docs'] WHERE type = 'scribe'"
    )
    assert await db_with_migrations.fetchval("SELECT roles FROM agent_definitions WHERE type = 'scribe'") == [
        "coder",
        "docs",
    ]


async def test_the_column_says_what_the_role_does(db_with_migrations: DatabasePool) -> None:
    comment = await db_with_migrations.fetchval(
        "SELECT col_description('agent_definitions'::regclass, attnum) FROM pg_attribute "
        "WHERE attrelid = 'agent_definitions'::regclass AND attname = 'roles'"
    )
    assert comment is not None and comment.startswith("#395: "), comment
    assert "coder runs it on the developer node" in comment and "docs lets it put files" in comment, comment
