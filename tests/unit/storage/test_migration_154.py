"""Migration 154 — people and their identities, one person backfilled for each user (#179).

Each user who signed in before the release becomes one person linked to them, with their name (else their email),
email, disabled flag, and GitHub login as a ``github`` identity, each audited as ``person.created``. A user's email
or login that only blanks set apart from another's, which the old indexes allowed, is left off instead of failing
the migration. The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS
from tests.fixtures.postgres import postgres_unavailable

pytestmark = pytest.mark.changes_schema

#: This migration's number.
VERSION = 154

USERS = [
    # id, email, name, disabled, github_login
    ("usr-ada", "ada@example.com", "Ada L", False, "Ada-L"),
    ("usr-bo", "bo@example.com", "", True, None),
    ("usr-cy", "cy@example.com", "  ", False, ""),
    ("usr-di", "di@example.com", "Di", False, "di"),
    ("usr-di2", " DI@example.com", "Di again", False, "di "),
]


def _sql() -> str:
    return next((s for v, _, s in MIGRATIONS if v == VERSION), "")


def test_the_people_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_153.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must store people and their identities"
    assert [v for v, _, _ in MIGRATIONS].count(VERSION) == 1, f"migration {VERSION} must be listed once"


@pytest.fixture
async def fresh_db() -> AsyncIterator[DatabasePool]:
    """A connected pool on a wiped schema; each test migrates it as far as it needs."""
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    db = DatabasePool(url)
    try:
        await db.connect()
    except (OSError, asyncpg.PostgresError) as exc:
        postgres_unavailable(exc)
    await db.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    yield db
    await db.close()


async def _migrate(db: DatabasePool, versions: range) -> None:
    """Apply the migrations numbered in ``versions``, in order, as the runner would."""
    assert db.pool is not None
    async with db.pool.acquire() as conn:
        for version, _, sql in MIGRATIONS:
            if version in versions:
                async with conn.transaction():
                    await conn.execute(sql)


async def test_each_user_becomes_one_person_with_their_github_login(fresh_db: DatabasePool) -> None:
    assert _sql(), f"there is no migration {VERSION}: nobody is a person"
    await _migrate(fresh_db, range(1, VERSION))
    for user_id, email, name, disabled, login in USERS:
        await fresh_db.execute(
            "INSERT INTO users (id, auth0_sub, email, name, disabled, github_login) VALUES ($1, $1, $2, $3, $4, $5)",
            user_id,
            email,
            name,
            disabled,
            login,
        )

    await _migrate(fresh_db, range(VERSION, VERSION + 1))

    people = {
        row["user_id"]: dict(row)
        for row in await fresh_db.fetch("SELECT id, name, email, user_id, disabled, created_by FROM people")
    }
    assert sorted(people) == sorted(u[0] for u in USERS), f"each user must be one person: {sorted(people)}"
    assert {u: (p["name"], p["email"], p["disabled"]) for u, p in people.items()} == {
        "usr-ada": ("Ada L", "ada@example.com", False),
        "usr-bo": ("bo@example.com", "bo@example.com", True),
        "usr-cy": ("cy@example.com", "cy@example.com", False),
        "usr-di": ("Di", None, False),
        "usr-di2": ("Di again", None, False),
    }, "a person has the user's name, else their email; an email only blanks set apart from another's is left off"
    identities = await fresh_db.fetch(
        "SELECT p.user_id, i.kind, i.value FROM person_identities i JOIN people p ON p.id = i.person_id"
    )
    assert sorted((r["user_id"], r["kind"], r["value"]) for r in identities) in (
        [("usr-ada", "github", "Ada-L"), ("usr-di", "github", "di")],
        [("usr-ada", "github", "Ada-L"), ("usr-di2", "github", "di")],
    ), f"each GitHub login is one github identity, once whatever blanks set it apart: {identities}"
    audits = await fresh_db.fetch("SELECT action, actor, details FROM audit_log WHERE action LIKE 'person.%'")
    assert sorted(r["details"]["user_id"] for r in audits) == sorted(people), "each person's creation is audited"
    ada = next(r for r in audits if r["details"]["user_id"] == "usr-ada")
    assert (ada["action"], ada["actor"], ada["details"]["identities"]) == (
        "person.created",
        "embry0:migration",
        [{"kind": "github", "value": "Ada-L"}],
    )
    assert all(p["created_by"] == "embry0:migration" and p["id"].startswith("per-") for p in people.values())

    await fresh_db.execute(_sql())
    assert await fresh_db.fetchval("SELECT count(*) FROM people") == len(USERS), "a second run makes nobody again"
    assert await fresh_db.fetchval("SELECT count(*) FROM audit_log WHERE action LIKE 'person.%'") == len(USERS)
