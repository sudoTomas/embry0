"""Migration 154 — people and their identities, one person backfilled for each user (#179).

Each user who signed in before the release becomes one person linked to them, with their name (else their email),
email, disabled flag, and GitHub login as a ``github`` identity, each audited as ``person.created``. Only what the
repository reads back is stored: a name cleaned as the name check requires, and a login only when it is a GitHub login
(``forge_logins.GITHUB_LOGIN_RE``); one left off is listed in the person's audit row and counted in a warning. A
user's email or login that only blanks or case set apart from another's goes to the user whose stored value is
trimmed already, then the enabled one, then the oldest. The number is ``VERSION`` alone: renumbering the migration
changes that line and this file's name.
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

pytestmark = pytest.mark.changes_schema

#: This migration's number.
VERSION = 154

USERS = [
    # id, email, name, disabled, github_login, created (days before now)
    ("usr-ada", "ada@example.com", "Ada L", False, "Ada-L", 9),
    ("usr-bo", "bo@example.com", "", True, None, 9),
    ("usr-cy", "cy@example.com", "  ", False, "", 9),
    # The same login and email but for blanks and case: the trimmed one wins, though it is the newer.
    ("usr-di2", " DI@example.com", "Di again", False, "DI ", 9),
    ("usr-di", "di@example.com", "Di", False, "di", 1),
    # The same login but for blanks, neither trimmed (the old index refuses two that case alone sets apart): the
    # enabled one wins, though it is the newer.
    ("usr-ev", "ev@example.com", "Ev", True, " Ev", 9),
    ("usr-ev2", "ev2@example.com", "Ev two", False, "ev ", 1),
    # Neither trimmed, both enabled: the older one wins.
    ("usr-fy", "fy@example.com", "Fy", False, " fy", 9),
    ("usr-fy2", "fy2@example.com", "Fy two", False, "FY ", 1),
    # Logins GitHub would not take, which the old route did; a name no person may have as it is.
    ("usr-gu", "gu@example.com", "Line\nbreak\u200b and\u2028more", False, "gu-", 9),
    ("usr-hu", "hu@example.com", "\u00a0\x07\u00a0", False, "h--u", 9),
]


def _sql() -> str:
    return next((s for v, _, s in MIGRATIONS if v == VERSION), "")


def test_the_people_migration_is_listed_once() -> None:
    """The last-entry assertion moved to tests/unit/storage/test_migration_155.py."""
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
    for user_id, email, name, disabled, login, days in USERS:
        await fresh_db.execute(
            "INSERT INTO users (id, auth0_sub, email, name, disabled, github_login, created_at) "
            "VALUES ($1, $1, $2, $3, $4, $5, now() - make_interval(days => $6))",
            user_id,
            email,
            name,
            disabled,
            login,
            days,
        )

    warnings: list[str] = []
    assert fresh_db.pool is not None

    def heard(_conn: Any, message: Any) -> None:
        warnings.append(str(message.message))

    async with fresh_db.pool.acquire() as conn:
        conn.add_log_listener(heard)
        try:
            async with conn.transaction():
                await conn.execute(_sql())
        finally:
            conn.remove_log_listener(heard)

    people = {
        row["user_id"]: dict(row)
        for row in await fresh_db.fetch("SELECT id, name, email, user_id, disabled, created_by FROM people")
    }
    assert sorted(people) == sorted(u[0] for u in USERS), f"each user must be one person: {sorted(people)}"
    assert {u: (p["name"], p["email"], p["disabled"]) for u, p in people.items()} == {
        "usr-ada": ("Ada L", "ada@example.com", False),
        "usr-bo": ("bo@example.com", "bo@example.com", True),
        "usr-cy": ("cy@example.com", "cy@example.com", False),
        "usr-di": ("Di", "di@example.com", False),
        "usr-di2": ("Di again", None, False),
        "usr-ev": ("Ev", "ev@example.com", True),
        "usr-ev2": ("Ev two", "ev2@example.com", False),
        "usr-fy": ("Fy", "fy@example.com", False),
        "usr-fy2": ("Fy two", "fy2@example.com", False),
        "usr-gu": ("Line break  and more", "gu@example.com", False),
        "usr-hu": ("hu@example.com", "hu@example.com", False),
    }, "a person has the user's name, cleaned, else their email; an email set apart by blanks goes to the trimmed one"
    from embry0.services.people import check_name

    for user_id, person in people.items():
        assert check_name(person["name"]) == person["name"], f"{user_id}'s name is one the name check refuses"
    identities = await fresh_db.fetch(
        "SELECT p.user_id, i.kind, i.value FROM person_identities i JOIN people p ON p.id = i.person_id"
    )
    assert sorted((r["user_id"], r["kind"], r["value"]) for r in identities) == [
        ("usr-ada", "github", "Ada-L"),
        ("usr-di", "github", "di"),
        ("usr-ev2", "github", "ev"),
        ("usr-fy", "github", "fy"),
    ], (
        "each GitHub login is one identity, to the trimmed user, then the enabled, then the oldest, and only a "
        f"GitHub login: {identities}"
    )
    audits = await fresh_db.fetch("SELECT action, actor, details FROM audit_log WHERE action LIKE 'person.%'")
    assert sorted(r["details"]["user_id"] for r in audits) == sorted(people), "each person's creation is audited"
    ada = next(r for r in audits if r["details"]["user_id"] == "usr-ada")
    assert (ada["action"], ada["actor"], ada["details"]["identities"], ada["details"]["left_off"]) == (
        "person.created",
        "embry0:migration",
        [{"kind": "github", "value": "Ada-L"}],
        [],
    )
    left_off = {r["details"]["user_id"]: r["details"]["left_off"] for r in audits if r["details"]["left_off"]}
    assert {u: [(o["value"], o["reason"]) for o in off] for u, off in left_off.items()} == {
        "usr-di2": [("DI", "another user's, set apart only by case or blanks")],
        "usr-ev": [("Ev", "another user's, set apart only by case or blanks")],
        "usr-fy2": [("FY", "another user's, set apart only by case or blanks")],
        "usr-gu": [("gu-", "not a GitHub login")],
        "usr-hu": [("h--u", "not a GitHub login")],
    }, f"each login no person was given is in its person's audit row, with why: {left_off}"
    assert any("5 user(s) kept a GitHub login" in w for w in warnings), f"the left-off logins are counted: {warnings}"
    logins = dict(await fresh_db.fetch("SELECT id, github_login FROM users WHERE id IN ('usr-gu', 'usr-hu')"))
    assert logins == {"usr-gu": "gu-", "usr-hu": "h--u"}, "a user keeps the login they had, matching what it did"
    assert all(p["created_by"] == "embry0:migration" and p["id"].startswith("per-") for p in people.values())

    await fresh_db.execute(_sql())
    assert await fresh_db.fetchval("SELECT count(*) FROM people") == len(USERS), "a second run makes nobody again"
    assert await fresh_db.fetchval("SELECT count(*) FROM audit_log WHERE action LIKE 'person.%'") == len(USERS)


#: Logins on either side of GitHub's rule.
LOGINS = ["a", "a-b", "a-b-c", "A1-b2", "x" * 39, "x" * 40, "-a", "a-", "a--b", "a_b", "a.b", "a b", "é", "", "1"]


async def test_the_backfill_reads_a_github_login_as_forge_logins_does(fresh_db: DatabasePool) -> None:
    """The migration's SQL cannot import the rule: its own spelling of it must agree with it."""
    import re

    from embry0.forge_logins import GITHUB_LOGIN_RE

    found = re.search(r"n\.login ~ '([^']+)' AND char_length\(n\.login\) <= (\d+)", _sql())
    assert found is not None, "the backfill does not check a login before it makes it an identity"
    pattern, longest = found.group(1), int(found.group(2))
    for login in LOGINS:
        sql = await fresh_db.fetchval("SELECT $1 ~ $2 AND char_length($1) <= $3", login, pattern, longest)
        assert sql == bool(GITHUB_LOGIN_RE.fullmatch(login)), f"the backfill and forge_logins disagree on {login!r}"


async def test_the_backfill_cleans_every_character_the_name_check_refuses(fresh_db: DatabasePool) -> None:
    """Every format, control and private-use character, and a line or paragraph separator, is cleaned from a name."""
    import re
    import unicodedata

    from embry0.services.people import check_name

    found = re.search(r"regexp_replace\(u\.name, '(\[[^']+\]\+)', ' ', 'g'\)", _sql())
    assert found is not None, "the backfill does not clean a name"
    refused = [
        chr(cp)
        for cp in range(1, 0x110000)
        if not 0xD800 <= cp <= 0xDFFF
        and (unicodedata.category(chr(cp)) in ("Cc", "Cf", "Co", "Zl", "Zp"))
        and (cp < 0xE000 or cp > 0xF8FF or cp % 512 == 0)
    ]
    names = [f"a{ch}b" for ch in refused]
    cleaned = await fresh_db.fetchval(
        "SELECT array_agg(regexp_replace(n, $2, ' ', 'g') ORDER BY i) FROM unnest($1::text[]) WITH ORDINALITY AS t(n, i)",
        names,
        found.group(1),
    )
    missed = [hex(ord(name[1])) for name, clean in zip(names, cleaned, strict=True) if check_name(clean) != clean]
    assert not missed, f"the backfill keeps characters the name check refuses: {missed[:20]}"
