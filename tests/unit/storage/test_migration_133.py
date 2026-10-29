"""Migration 133 — each knowledge map's owner, keyed by owner and slug, with its marks, sends and leads (#180).

The number is ``VERSION`` alone: renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import asyncpg
import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS
from tests.fixtures.postgres import postgres_unavailable

#: This migration's number.
VERSION = 133


def _sql() -> str:
    return next((s for v, _, s in MIGRATIONS if v == VERSION), "")


def test_the_map_owner_migration_is_the_last_entry() -> None:
    """The newest migration owns this assertion; it passes to the next one
    (it came from tests/unit/storage/test_migration_132.py)."""
    assert MIGRATIONS[-1][0] == VERSION, f"migration {VERSION} must give each knowledge map an owner"
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


_GRAPH = json.dumps({"slug": "x", "title": "X", "repo": None, "nodes": [], "edges": []}, separators=(",", ":"))
#: Text the release before stored from an upload Python's JSON reads and JSONB refuses: NaN, a NUL, a lone surrogate.
_NOT_JSONB = (
    '{"slug":"org","title":"Org","repo":null,"nodes":[{"id":"a","x":NaN,"label":"a\\u0000b\\ud800"}],"edges":[]}'
)
GRAPHS = {"shop": _GRAPH, "org": _NOT_JSONB, "shop-2": _GRAPH}


async def _seed(db: DatabasePool) -> None:
    """Three maps as the release before kept them, each with a mark, a send and a weed lead."""
    for slug, repo in (("shop", "Acme/Shop"), ("org", None), ("shop-2", "zeta/shop")):
        await db.execute(
            "INSERT INTO knowledge_maps (slug, title, repo, graph) VALUES ($1, $1, $2, $3)", slug, repo, GRAPHS[slug]
        )
        await db.execute(
            "INSERT INTO knowledge_map_marks (map_slug, node_id, marked_by, verdict) VALUES ($1, 'a', 'ann', 'bloat')",
            slug,
        )
        await db.execute(
            "INSERT INTO knowledge_map_sends (map_slug, node_id, kind, repo, sent_by)"
            " VALUES ($1, 'a', 'analysis', 'acme/shop', 'ann')",
            slug,
        )
        await db.execute(
            "INSERT INTO leads (id, kind, repo, source_key, title, data) VALUES ($1, 'weed', 'acme/shop', $2, 't', '{}')",
            f"weed:{slug}",
            f"map:{slug}:a",
        )
    # Marks of a map that was deleted: no map says whose they were.
    await db.execute(
        "INSERT INTO knowledge_map_marks (map_slug, node_id, marked_by, verdict) VALUES ('gone', 'a', 'ann', 'keep')"
    )


@pytest.mark.requires_postgres
async def test_the_backfill_gives_each_map_its_repositorys_owner_and_its_rows_follow(fresh_db: DatabasePool) -> None:
    assert _sql(), f"migration {VERSION} must give each knowledge map an owner"
    db = fresh_db
    await _migrate(db, range(1, VERSION))
    await _seed(db)
    try:
        await _migrate(db, range(VERSION, VERSION + 1))
    except asyncpg.PostgresError as exc:
        pytest.fail(
            f"migration {VERSION} must apply over any stored graph text, or the orchestrator cannot start: {exc}"
        )

    maps = {r["slug"]: r for r in await db.fetch("SELECT slug, owner, graph FROM knowledge_maps")}
    assert {s: m["owner"] for s, m in maps.items()} == {"shop": "acme", "org": None, "shop-2": "zeta"}, (
        "a map with a repo must get its repo's owner, lowercased; one without, no owner"
    )
    assert {s: m["graph"] for s, m in maps.items()} == GRAPHS, "the stored graph text must be left as it was"
    marks = {
        r["map_slug"]: r["map_owner"] for r in await db.fetch("SELECT map_slug, map_owner FROM knowledge_map_marks")
    }
    assert marks == {"shop": "acme", "org": None, "shop-2": "zeta", "gone": None}, (
        "a mark must get its map's owner, and one whose map is gone none"
    )
    sends = {
        r["map_slug"]: r["map_owner"] for r in await db.fetch("SELECT map_slug, map_owner FROM knowledge_map_sends")
    }
    assert sends == {"shop": "acme", "org": None, "shop-2": "zeta"}, "a send must get its map's owner"
    leads = {
        r["id"]: json.loads(r["data"]) if isinstance(r["data"], str) else r["data"]
        for r in await db.fetch("SELECT id, data FROM leads")
    }
    assert {k: v.get("map_owner", "missing") for k, v in leads.items()} == {
        "weed:shop": "acme",
        "weed:org": None,
        "weed:shop-2": "zeta",
    }, "a map's weed lead must record its map's owner, and map:shop's must not take map:shop-2's"


@pytest.mark.requires_postgres
async def test_two_owners_share_a_slug_and_one_owner_cannot_have_two(db_with_migrations: DatabasePool) -> None:
    assert _sql(), f"migration {VERSION} must key knowledge maps by owner and slug"
    db = db_with_migrations
    insert = "INSERT INTO knowledge_maps (slug, owner, title, graph) VALUES ('shop', $1, 'Shop', '{}')"
    for owner in ("acme", "zeta", None):
        await db.execute(insert, owner)
    assert await db.fetchval("SELECT count(*) FROM knowledge_maps WHERE slug = 'shop'") == 3, (
        "two owners and the unowned map must each keep a map called shop"
    )
    for owner in ("acme", None):
        with pytest.raises(asyncpg.UniqueViolationError):
            await db.execute(insert, owner)
    mark = "INSERT INTO knowledge_map_marks (map_slug, map_owner, node_id, marked_by, verdict) VALUES ('shop', $1, 'a', 'ann', 'keep')"
    for owner in ("acme", "zeta", None):
        await db.execute(mark, owner)
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.execute(mark, None)


def test_the_key_treats_no_owner_as_one_owner() -> None:
    """Two unowned maps of one slug conflict, as two of one owner do: the upload's ON CONFLICT relies on it."""
    sql = _sql()
    assert "ON knowledge_maps (slug, owner) NULLS NOT DISTINCT" in sql, f"migration {VERSION} must key maps by both"
    assert "ON knowledge_map_marks (map_slug, node_id, marked_by, map_owner) NULLS NOT DISTINCT" in sql


@pytest.mark.requires_postgres
async def test_running_it_again_moves_nothing_once_owners_share_a_slug(db_with_migrations: DatabasePool) -> None:
    """A backfill takes a slug only while one map has it: an unowned map's rows never go to an owned one."""
    db = db_with_migrations
    for owner in (None, "acme"):
        await db.execute(
            "INSERT INTO knowledge_maps (slug, owner, title, graph) VALUES ('shop', $1, 'Shop', '{}')", owner
        )
    await db.execute(
        "INSERT INTO knowledge_map_marks (map_slug, map_owner, node_id, marked_by, verdict) VALUES ('shop', NULL, 'a', 'ann', 'keep')"
    )
    await db.execute(
        "INSERT INTO knowledge_map_sends (map_slug, map_owner, node_id, kind, repo, sent_by)"
        " VALUES ('shop', NULL, 'a', 'analysis', 'acme/shop', 'ann')"
    )
    await db.execute(
        "INSERT INTO leads (id, kind, repo, source_key, title, data) VALUES ('weed:x', 'weed', 'acme/shop', 'map:shop:a', 't', '{}')"
    )
    await db.execute(_sql())
    assert await db.fetchval("SELECT map_owner FROM knowledge_map_marks") is None, (
        "running the migration again must not hand the unowned map's marks to acme's map"
    )
    assert await db.fetchval("SELECT map_owner FROM knowledge_map_sends") is None, "nor its sends"
    assert await db.fetchval("SELECT data ? 'map_owner' FROM leads") is False, "nor guess whose map made a lead"
    assert await db.fetchval("SELECT count(*) FROM knowledge_maps WHERE owner IS NULL") == 1
