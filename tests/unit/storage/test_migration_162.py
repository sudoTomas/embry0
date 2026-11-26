"""Migration 162: ``users.github_login`` is retired; owners, asks and deciders read the people's identities alone.

The user's GitHub login stood in for a user with no person, and was kept in step with a linked person's GitHub
identities, for one release after owners resolved to people (#179, spec 2026-11-23, section 3). A login no person
holds as an identity is written to a ``user.github_login_retired`` audit row before the column goes, for an admin to
add on the People page. The number is ``VERSION`` alone: renumbering the migration changes that line and this file's
name.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS
from tests.fixtures.postgres import postgres_unavailable

#: This migration's number.
VERSION = 162

pytestmark = [pytest.mark.requires_postgres, pytest.mark.changes_schema]


@pytest.fixture
async def fresh_db() -> AsyncIterator[DatabasePool]:
    """A connected pool on a wiped schema; the test migrates it as far as it needs."""
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    db = DatabasePool(url)
    try:
        await db.connect()
    except (OSError, asyncpg.PostgresError) as exc:
        postgres_unavailable(exc)
    await db.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    yield db
    await db.close()


async def migrate_to(db: DatabasePool, last: int, first: int = 1) -> None:
    """Apply the migrations numbered ``first`` to ``last``, in order, as the runner would."""
    assert db.pool is not None
    async with db.pool.acquire() as conn:
        for version, _, sql in MIGRATIONS:
            if first <= version <= last:
                async with conn.transaction():
                    await conn.execute(sql)


def test_the_github_login_retirement_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_161.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must retire users.github_login"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


async def test_the_column_is_gone_and_a_login_no_person_holds_is_kept_in_the_audit_log(fresh_db: DatabasePool) -> None:
    await migrate_to(fresh_db, VERSION - 1)
    db: Any = fresh_db
    for user_id, email, login in (
        ("usr-held", "held@example.com", "held-login"),
        ("usr-loose", "loose@example.com", "Loose-Login"),
        ("usr-none", "none@example.com", None),
    ):
        await db.execute(
            "INSERT INTO users (id, auth0_sub, email, name, tier, github_login) VALUES ($1, $1, $2, '', 'operator', $3)",
            user_id,
            email,
            login,
        )
    await db.execute("INSERT INTO people (id, name, user_id) VALUES ('per-held', 'Held', 'usr-held')")
    await db.execute(
        "INSERT INTO person_identities (person_id, kind, value) VALUES ('per-held', 'github', 'HELD-login')"
    )
    await migrate_to(fresh_db, VERSION, VERSION)
    column = await db.fetchval(
        "SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'github_login'"
    )
    assert column is None, "users.github_login is still there"
    kept = [
        dict(row["details"])
        for row in await db.fetch(
            "SELECT details FROM audit_log WHERE action = 'user.github_login_retired' AND actor = 'embry0:migration'"
        )
    ]
    assert kept == [{"user_id": "usr-loose", "email": "loose@example.com", "login": "Loose-Login"}], (
        f"only a login no person holds, any case, is kept for an admin to add: {kept}"
    )
    await migrate_to(fresh_db, VERSION, VERSION)
    again = await db.fetchval("SELECT count(*) FROM audit_log WHERE action = 'user.github_login_retired'")
    assert again == 1, "run again, it writes nothing"
