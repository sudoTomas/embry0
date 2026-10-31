"""The test database: what a test does when it cannot be reached, and how one starts clean.

A bare local run, with ``TEST_DATABASE_URL`` unset, skips: the suite stays
usable on a machine with no Postgres. A run that sets ``TEST_DATABASE_URL``
has said a database is part of it, so an unreachable one FAILS — otherwise a
misconfigured or slow database turns ~800 DB tests into skips nobody reads,
and the SQL layer goes untested while the run looks clean (#509).

Every DB fixture and test that catches a connection error calls this one
helper, so the rule cannot drift between copies.

A test that wants a clean database gets the rows the migrations write and
nothing else, whatever ran before it. A list of tables to empty misses the
next table a feature adds, and a test reading that table sees the rows an
earlier test left there (#235).

``db_with_migrations`` and ``api_client`` both get it from
:func:`start_clean`:

- Most tests get it from :func:`reset_to_fresh`. That migrates once per
  database and remembers what the migrations left: the seeded rows, every
  sequence, and a digest of the schema. Before each later test it deletes
  the rows of every table that holds some, the migration ledger aside, and
  puts the remembered rows and sequences back: about 13 ms against 2.5 s to
  migrate again, on a Postgres that syncs to disk as CI's does. When the
  schema differs from the one it remembered, because a test in between
  changed it, it migrates from empty again.
- A test marked ``changes_schema`` gets :func:`migrate_from_empty`: the
  schema dropped and migrated again. Every test in a ``test_migration*``
  module is marked (tests/conftest.py), and so is any other test that
  changes the schema. Such a test may leave the schema in a state the digest
  does not see, so :func:`after_test` makes the test after it migrate from
  empty as well, whichever fixture or connection it used.
- After every other database test, :func:`after_test` takes the digest
  again, and the test fails, naming itself, if it changed the schema: it
  should be marked. :func:`start_clean` catches a change no such check saw,
  made on a connection of the test's own, and fails naming the test before.

:func:`assert_no_rows_left` then fails the fixture, naming the table, if any
table holds rows the migrations did not write.

Under pytest-xdist (``pytest -n``) each worker runs on a database of its own,
named after ``TEST_DATABASE_URL``'s with the worker's id appended
(:func:`worker_database_url`); tests/conftest.py creates it when the worker
starts and drops it when the worker ends, and the run that started the
workers drops any a worker left behind (:func:`drop_worker_databases`).
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from embry0.storage.migrations.runner import run_migrations

if TYPE_CHECKING:
    from embry0.storage.database import DatabasePool

#: The migration ledger: which migrations ran. A reset never empties it.
MIGRATION_LEDGER = "embry0_migrations"

#: The tables the migrations themselves write rows into: the ledger and their
#: seeds. Every other table of a freshly migrated database is empty.
#: tests/unit/api/test_clean_database.py holds this to what the migrations write.
MIGRATION_SEEDED_TABLES: frozenset[str] = frozenset({MIGRATION_LEDGER, "agent_definitions", "settings_documents"})


def postgres_unavailable(exc: BaseException) -> NoReturn:
    """Skip without TEST_DATABASE_URL; fail with it. Never returns."""
    if os.environ.get("TEST_DATABASE_URL"):
        pytest.fail(
            f"TEST_DATABASE_URL is set but PostgreSQL is unreachable: {exc}",
            pytrace=False,
        )
    pytest.skip(f"PostgreSQL unavailable: {exc}")


async def migrate_from_empty(db: DatabasePool) -> None:
    """Drop every table, then run the migrations: a fresh deployment's database, and no test's rows."""
    await db.execute("DROP SCHEMA public CASCADE")
    await db.execute("CREATE SCHEMA public")
    await run_migrations(db)


async def tables_with_rows(db: DatabasePool) -> list[str]:
    """The tables of the public schema that hold at least one row, by name."""
    tables = [
        str(r["tablename"])
        for r in await db.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    ]
    if not tables:
        return []
    probe = " UNION ALL ".join(
        f'SELECT {i} AS n WHERE EXISTS (SELECT 1 FROM public."{name}")' for i, name in enumerate(tables)
    )
    return [tables[r["n"]] for r in await db.fetch(probe)]


async def assert_no_rows_left(db: DatabasePool, seeded: Mapping[str, int] | None = None) -> None:
    """Fail unless every table holds only what the migrations write, naming each one that holds more.

    ``seeded`` is how many rows the migrations write into each seeded table; without it, only the
    tables they leave empty are checked.
    """
    left = sorted(set(await tables_with_rows(db)) - MIGRATION_SEEDED_TABLES)
    if left:
        pytest.fail(
            f"the test database does not start clean: {', '.join(left)} hold rows an earlier test left "
            "(or a migration now writes; then add the table to MIGRATION_SEEDED_TABLES)",
            pytrace=False,
        )
    for table, expected in sorted((seeded or {}).items()):
        found = await db.fetchval(f'SELECT count(*) FROM public."{table}"')
        if found != expected:
            pytest.fail(
                f"the test database does not start clean: {table} holds {found} rows, the migrations write {expected}",
                pytrace=False,
            )


# Everything a migration defines in the public schema, and the ledger, as one digest. Names
# and definitions only, never an oid, so two databases migrated alike digest alike. A column's
# number is its place in SELECT *, so dropping one and adding it back changes the digest. A
# foreign key's triggers are named by oid, so they are keyed by their constraint's name.
_SHAPE_SQL = """
SELECT md5(string_agg(part, E'\\n' ORDER BY part)) FROM (
    SELECT format('column %s.%s %s %s %s %s %s%s %s %s', c.relname, a.attname, a.attnum,
                  format_type(a.atttypid, a.atttypmod), a.attnotnull, pg_get_expr(d.adbin, d.adrelid),
                  a.attidentity, a.attgenerated, a.attcollation::regcollation, a.attacl)
    FROM pg_attribute a
    JOIN pg_class c ON c.oid = a.attrelid
    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p', 'v', 'm')
      AND a.attnum > 0 AND NOT a.attisdropped
    UNION ALL
    SELECT format('relation %s %s %s %s %s %s %s', relname, relkind, relpersistence, reloptions,
                  relrowsecurity, relforcerowsecurity, relacl)
    FROM pg_class WHERE relnamespace = 'public'::regnamespace
    UNION ALL
    SELECT format('comment %s %s %s', c.relname, d.objsubid, md5(d.description))
    FROM pg_description d JOIN pg_class c ON c.oid = d.objoid
    WHERE d.classoid = 'pg_class'::regclass AND c.relnamespace = 'public'::regnamespace
    UNION ALL
    SELECT format('policy %s %s %s %s %s %s %s', tablename, policyname, permissive, roles, cmd, qual, with_check)
    FROM pg_policies WHERE schemaname = 'public'
    UNION ALL
    SELECT format('rule %s %s %s', tablename, rulename, md5(definition)) FROM pg_rules WHERE schemaname = 'public'
    UNION ALL
    SELECT format('constraint %s %s %s', conrelid::regclass, conname, pg_get_constraintdef(oid))
    FROM pg_constraint WHERE connamespace = 'public'::regnamespace
    UNION ALL
    SELECT format('index %s', indexdef) FROM pg_indexes WHERE schemaname = 'public'
    UNION ALL
    SELECT format('trigger %s %s', tgenabled, pg_get_triggerdef(t.oid))
    FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
    WHERE NOT t.tgisinternal AND c.relnamespace = 'public'::regnamespace
    UNION ALL
    SELECT format('internal trigger %s %s %s %s %s', c.relname, k.conname, t.tgfoid::regproc, t.tgtype, t.tgenabled)
    FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_constraint k ON k.oid = t.tgconstraint
    WHERE t.tgisinternal AND c.relnamespace = 'public'::regnamespace
    UNION ALL
    SELECT format('function %s', md5(pg_get_functiondef(oid)))
    FROM pg_proc WHERE pronamespace = 'public'::regnamespace AND prokind IN ('f', 'p')
    UNION ALL
    SELECT format('aggregate %s %s %s %s %s %s', p.oid::regprocedure, p.prokind, p.prosrc, a.aggtransfn,
                  a.aggfinalfn, a.aggtranstype::regtype)
    FROM pg_proc p LEFT JOIN pg_aggregate a ON a.aggfnoid = p.oid
    WHERE p.pronamespace = 'public'::regnamespace AND p.prokind IN ('a', 'w')
    UNION ALL
    SELECT format('view %s %s', viewname, md5(definition)) FROM pg_views WHERE schemaname = 'public'
    UNION ALL
    SELECT format('sequence %s %s %s %s %s %s %s %s', sequencename, data_type, start_value, increment_by,
                  min_value, max_value, cache_size, cycle)
    FROM pg_sequences WHERE schemaname = 'public'
    UNION ALL
    SELECT format('schema %s %s', nspowner::regrole, nspacl) FROM pg_namespace WHERE nspname = 'public'
    UNION ALL
    SELECT format('default privileges %s %s %s %s', defaclrole::regrole, defaclnamespace::regnamespace,
                  defaclobjtype, defaclacl)
    FROM pg_default_acl
    UNION ALL
    SELECT format('type %s %s %s %s', typname, typtype, format_type(typbasetype, typtypmod), typnotnull)
    FROM pg_type WHERE typnamespace = 'public'::regnamespace
    UNION ALL
    SELECT format('enum %s %s', t.typname, string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder))
    FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
    WHERE t.typnamespace = 'public'::regnamespace GROUP BY t.typname
    UNION ALL
    SELECT format('migration %s %s', version, description) FROM public.embry0_migrations
) parts(part)
"""


async def schema_shape(db: DatabasePool | asyncpg.Connection) -> str:
    """A digest of the public schema's definition and the migrations it ran; empty when it has no ledger."""
    try:
        return str(await db.fetchval(_SHAPE_SQL) or "")
    except asyncpg.UndefinedTableError:
        return ""


@dataclass(frozen=True)
class FreshDatabase:
    """What migrating an empty schema leaves: enough to put a database back without migrating it again."""

    shape: str
    #: Each seeded table but the ledger: the columns written back, and its rows as a JSON array.
    rows: dict[str, tuple[tuple[str, ...], str]]
    #: How many rows the migrations write into each of those tables.
    counts: dict[str, int]
    #: Each sequence, ``schema.name``, with the value and is_called flag ``setval`` restores.
    sequences: tuple[tuple[str, int, bool], ...]
    #: Every table, each one that references another by a foreign key before it, so deleting in
    #: this order never trips a key; None when the keys form a cycle.
    delete_order: tuple[str, ...] | None


async def capture_fresh(db: DatabasePool) -> FreshDatabase:
    """Remember a freshly migrated database: its shape, its seeded rows and its sequences.

    Fails when the migrations filled other tables than MIGRATION_SEEDED_TABLES: a reset that did not
    put those rows back would hand tests a database no deployment has.
    """
    filled = set(await tables_with_rows(db))
    if filled != MIGRATION_SEEDED_TABLES:
        pytest.fail(
            f"a fresh migration fills {sorted(filled)}, MIGRATION_SEEDED_TABLES lists {sorted(MIGRATION_SEEDED_TABLES)}",
            pytrace=False,
        )
    rows: dict[str, tuple[tuple[str, ...], str]] = {}
    counts: dict[str, int] = {}
    for table in sorted(MIGRATION_SEEDED_TABLES - {MIGRATION_LEDGER}):
        columns = tuple(
            str(r["attname"])
            for r in await db.fetch(
                "SELECT attname FROM pg_attribute WHERE attrelid = $1::regclass AND attnum > 0"
                " AND NOT attisdropped AND attgenerated = '' ORDER BY attnum",
                f'public."{table}"',
            )
        )
        record = await db.fetchrow(
            f"SELECT count(*) AS n, COALESCE(json_agg(t), '[]')::text AS rows FROM public.\"{table}\" AS t"
        )
        assert record is not None
        rows[table] = (columns, str(record["rows"]))
        counts[table] = int(record["n"])
    sequences = tuple(
        (str(r["name"]), int(r["value"]), bool(r["called"]))
        for r in await db.fetch(
            "SELECT format('%I.%I', schemaname, sequencename) AS name, COALESCE(last_value, start_value) AS value,"
            " last_value IS NOT NULL AS called FROM pg_sequences WHERE schemaname = 'public' ORDER BY 1"
        )
    )
    return FreshDatabase(
        shape=await schema_shape(db),
        rows=rows,
        counts=counts,
        sequences=sequences,
        delete_order=await _delete_order(db),
    )


async def _delete_order(db: DatabasePool) -> tuple[str, ...] | None:
    """The public tables, each one that references another before the one it references."""
    sorter: TopologicalSorter[str] = TopologicalSorter()
    for r in await db.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"):
        sorter.add(str(r["tablename"]))
    for r in await db.fetch(
        "SELECT c.relname AS child, p.relname AS parent FROM pg_constraint k"
        " JOIN pg_class c ON c.oid = k.conrelid JOIN pg_class p ON p.oid = k.confrelid"
        " WHERE k.contype = 'f' AND k.connamespace = 'public'::regnamespace"
    ):
        if r["child"] != r["parent"]:
            sorter.add(str(r["parent"]), str(r["child"]))
    try:
        return tuple(sorter.static_order())
    except CycleError:
        return None


async def restore_fresh(db: DatabasePool, fresh: FreshDatabase) -> None:
    """Empty every table but the ledger, then put the seeded rows and the sequences back, in one transaction.

    Only the tables that hold rows are emptied, with DELETE: a table without rows has nothing to
    empty, and every sequence is set back, the empty tables' too. TRUNCATE gives each table it
    empties new files; on a Postgres that syncs to disk, truncating every table took about 1.6 s a
    test, nearly as long as migrating again, and TRUNCATE ... CASCADE takes every table that
    references the ones named with it. The deletes follow ``fresh.delete_order``, so no foreign
    key is tripped. Should the keys ever form a cycle, there is no such order, and the tables that
    hold rows are truncated instead.
    """
    holding = set(await tables_with_rows(db)) - {MIGRATION_LEDGER}
    holding |= set(fresh.rows)
    async with db.transaction() as conn:
        # A connection an earlier test left open with a lock fails the reset, instead of hanging it.
        await conn.execute("SET LOCAL lock_timeout = '10s'")
        if fresh.delete_order is None:
            listed_tables = ", ".join(f'public."{t}"' for t in sorted(holding))
            await conn.execute(f"TRUNCATE TABLE {listed_tables} RESTART IDENTITY CASCADE")
        else:
            for table in fresh.delete_order:
                if table in holding:
                    await conn.execute(f'DELETE FROM public."{table}"')
        for table, (columns, payload) in fresh.rows.items():
            listed = ", ".join(f'"{c}"' for c in columns)
            await conn.execute(
                f'INSERT INTO public."{table}" ({listed}) OVERRIDING SYSTEM VALUE '
                f'SELECT {listed} FROM json_populate_recordset(NULL::public."{table}", $1::json)',
                payload,
            )
        if fresh.sequences:
            names, values, called = zip(*fresh.sequences, strict=True)
            await conn.execute(
                "SELECT setval(name::regclass, value, called) FROM unnest($1::text[], $2::bigint[], $3::bool[])"
                " AS s(name, value, called)",
                list(names),
                list(values),
                list(called),
            )


_FRESH: dict[str, FreshDatabase] = {}


async def reset_to_fresh(db: DatabasePool, database_url: str, *, shape: str | None = None) -> FreshDatabase:
    """Leave the database as migrating an empty schema does, and check it.

    Migrates from empty the first time for each database, and again whenever the schema differs
    from the one remembered then; otherwise restores what that migration left. ``shape`` is the
    schema's digest, when the caller has just taken it.
    """
    fresh = _FRESH.get(database_url)
    if fresh is None or (shape if shape is not None else await schema_shape(db)) != fresh.shape:
        await migrate_from_empty(db)
        fresh = _FRESH[database_url] = await capture_fresh(db)
    else:
        await restore_fresh(db, fresh)
    await assert_no_rows_left(db, fresh.counts)
    return fresh


#: Marks a test that tests the migrations or changes the schema. It gets a schema migrated from
#: empty, and so does the test after it.
CHANGES_SCHEMA = "changes_schema"

#: The databases :func:`start_clean` has given a test in this process: the ones :func:`after_test` checks.
_GIVEN: set[str] = set()
#: The last test :func:`after_test` saw in this process.
_PREVIOUS_TEST: list[str] = []


def changes_schema(item: pytest.Item) -> bool:
    """Whether ``item`` is marked ``changes_schema``."""
    return item.get_closest_marker(CHANGES_SCHEMA) is not None


def _uses_a_database(item: pytest.Item) -> bool:
    """Whether ``item`` uses a database fixture or is marked ``requires_postgres``."""
    fixtures = set(getattr(item, "fixturenames", ()))
    return (
        bool(fixtures & {"db_with_migrations", "api_client"})
        or item.get_closest_marker("requires_postgres") is not None
    )


def _unmarked_change(culprit: str) -> str:
    return (
        f"{culprit} changed the schema and is not marked changes_schema: the schema differs from a fresh "
        "migration's after it, so a later test would have seen the change. Mark the test "
        "@pytest.mark.changes_schema; its database is then migrated from empty, and the next test's too "
        "(tests/fixtures/postgres.py)."
    )


async def start_clean(db: DatabasePool, database_url: str, item: pytest.Item) -> None:
    """Give the test ``item`` a database as migrating an empty schema leaves it.

    A test marked ``changes_schema`` gets the schema dropped and migrated again; every other test gets
    :func:`reset_to_fresh`. If the schema differs from the one remembered here, a test that is not
    marked changed it where :func:`after_test` could not look: the schema is migrated again and this
    test fails, naming the test that ran before it.
    """
    _GIVEN.add(database_url)
    if changes_schema(item):
        await migrate_from_empty(db)
        return
    fresh = _FRESH.get(database_url)
    shape = await schema_shape(db) if fresh is not None else None
    await reset_to_fresh(db, database_url, shape=shape)
    if fresh is not None and shape != fresh.shape:
        culprit = _PREVIOUS_TEST[0] if _PREVIOUS_TEST else "a test before this one"
        pytest.fail(f"at the start of {item.nodeid}: {_unmarked_change(culprit)}", pytrace=False)


async def schema_kept(db: DatabasePool | asyncpg.Connection, database_url: str) -> bool:
    """Whether the schema is still the one remembered for ``database_url``, or none is remembered.

    When it is not, the remembered state is forgotten, so the next test migrates from empty.
    """
    fresh = _FRESH.get(database_url)
    if fresh is None or await schema_shape(db) == fresh.shape:
        return True
    del _FRESH[database_url]
    return False


async def _schema_kept_at(database_url: str) -> bool:
    try:
        conn = await asyncpg.connect(database_url)
    except (OSError, asyncpg.PostgresError):
        return True  # Unreachable now: the test that used it has already failed and said so.
    try:
        return await schema_kept(conn, database_url)
    finally:
        await conn.close()


def after_test(item: pytest.Item) -> None:
    """After every test: forget or check the databases :func:`start_clean` gave a test.

    After a test marked ``changes_schema``, every database remembered is forgotten, so the next test
    migrates from empty: the test may have left the schema in a state the digest does not see,
    through a fixture or a connection of its own. After any other test that uses a database, the
    digest of each database a test was given is taken again, and the test fails if it changed.
    tests/conftest.py calls this after every test, once its fixtures are torn down.
    """
    _PREVIOUS_TEST[:] = [item.nodeid]
    if changes_schema(item):
        _FRESH.clear()
        return
    if not _uses_a_database(item):
        return
    changed = [url for url in sorted(_GIVEN & _FRESH.keys()) if not asyncio.run(_schema_kept_at(url))]
    if changed:
        pytest.fail(_unmarked_change(item.nodeid), pytrace=False)


#: The longest database name Postgres keeps: it truncates a longer one, so two workers would share it.
MAX_DATABASE_NAME_BYTES = 63


def worker_database_url(database_url: str, worker: str) -> str:
    """``database_url`` naming a database of the pytest-xdist worker ``worker`` alone: ``<name>_<worker>``."""
    parts = urlsplit(database_url)
    name = parts.path.lstrip("/")
    if not name:
        raise ValueError("TEST_DATABASE_URL names no database")
    own = f"{name}_{worker}"
    if len(own.encode()) > MAX_DATABASE_NAME_BYTES:
        raise ValueError(
            f"the worker database {own!r} is longer than the {MAX_DATABASE_NAME_BYTES} bytes Postgres keeps of "
            "a name, so workers would share one database: give TEST_DATABASE_URL a shorter database name"
        )
    return urlunsplit(parts._replace(path=f"/{own}"))


def server_and_database(database_url: str) -> tuple[str, str]:
    """The URL of the server's ``postgres`` database, and the name of the database ``database_url`` names."""
    parts = urlsplit(database_url)
    return urlunsplit(parts._replace(path="/postgres")), parts.path.lstrip("/")


def quoted(name: str) -> str:
    """``name`` as a quoted SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


class DatabaseUnreachable(Exception):
    """The server :func:`create_database` was to create a database on could not be reached."""


async def create_database(database_url: str) -> bool:
    """Create the database ``database_url`` names, unless it exists. True when this call created it.

    Raises :class:`DatabaseUnreachable` when the server cannot be reached, and the server's own error
    when it refuses to create the database.
    """
    server, name = server_and_database(database_url)
    try:
        conn = await asyncpg.connect(server)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        raise DatabaseUnreachable(str(exc)) from exc
    try:
        if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            return False
        try:
            # template0: a template1 with a stale collation version fails CREATE DATABASE.
            await conn.execute(f"CREATE DATABASE {quoted(name)} TEMPLATE template0")
        except asyncpg.DuplicateDatabaseError:
            return False
        return True
    finally:
        await conn.close()


async def drop_database(database_url: str) -> None:
    """Drop the database ``database_url`` names, closing whatever connections are still open to it."""
    server, name = server_and_database(database_url)
    conn = await asyncpg.connect(server)
    try:
        await conn.execute(f"DROP DATABASE IF EXISTS {quoted(name)} WITH (FORCE)")
    finally:
        await conn.close()


async def drop_worker_databases(database_url: str) -> list[str]:
    """Drop every worker database of ``database_url``'s, ``<name>_gw<N>``, and return their names.

    A worker drops its own database when it ends; one killed outright cannot, and the run that
    started it sweeps up after it.
    """
    server, name = server_and_database(database_url)
    worker_database = re.compile(re.escape(name) + r"_gw[0-9]+")
    conn = await asyncpg.connect(server)
    try:
        names = sorted(
            str(r["datname"])
            for r in await conn.fetch("SELECT datname FROM pg_database")
            if worker_database.fullmatch(str(r["datname"]))
        )
        for each in names:
            await conn.execute(f"DROP DATABASE IF EXISTS {quoted(each)} WITH (FORCE)")
        return names
    finally:
        await conn.close()
