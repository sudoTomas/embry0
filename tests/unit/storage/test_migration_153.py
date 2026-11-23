"""Migration 153 — each stored knowledge map serves its groups, and each part its group, under their new names (#180).

embry0.map/1 calls the scanner's rings of parts ``groups`` now, and a part names its ring as ``group``; it reads
``areas`` and ``area``, their old names, as the same. An upload after this release stores both. A map stored before
it holds only the old names: the migration copies each in under the new one, by text alone, so a graph JSONB
refuses is copied too. The number is ``VERSION`` alone:
renumbering the migration changes that line and this file's name.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from embry0.services.knowledge_maps import FORMAT, parse_map
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS
from tests.fixtures.postgres import postgres_unavailable

pytestmark = pytest.mark.changes_schema

#: This migration's number.
VERSION = 153

GROUPS = [{"id": "ops", "label": 'Ops "core", nodes', "summary": None, "x": 1.0, "y": 2.0, "r": 3.0}]


def _old_graph(slug: str, nodes: list[dict[str, Any]] | None = None) -> str:
    """A graph as the release before stored it: parse_map's output, with ``areas`` and no ``groups``."""
    doc = {
        "format": FORMAT,
        "kinds": [{"id": "script", "label": 'A "nodes": kind'}],
        "areas": GROUPS,
        "nodes": nodes
        or [
            {"id": "a", "area": 'ops "x",\\ "label":', "label": ',"nodes":[ trick ,"area":"no","label":'},
            {"id": "b", "label": "no ring"},
        ],
        "edges": [],
    }
    graph = dict(parse_map(doc, slug).graph)
    graph.pop("groups")
    graph["nodes"] = [{k: v for k, v in node.items() if k != "group"} for node in graph["nodes"]]
    return json.dumps(graph, separators=(",", ":"))


#: Text the release before stored from an upload Python's JSON reads and JSONB refuses: NaN, a NUL, a lone surrogate.
NOT_JSONB = (
    '{"slug":"org","title":"Org","repo":null,"commit":null,"source":"","generated_at":null,"kinds":[],'
    '"areas":[{"id":"g","label":"G","summary":null,"x":NaN,"y":null,"r":null}],'
    '"nodes":[{"id":"a","x":NaN,"label":"a\\u0000b\\ud800"}],"edges":[]}'
)
NO_AREAS = json.dumps({"slug": "x", "title": "X", "repo": None, "nodes": [], "edges": []}, separators=(",", ":"))


def _sql() -> str:
    return next((s for v, _, s in MIGRATIONS if v == VERSION), "")


def test_the_map_groups_migration_is_listed_once() -> None:
    """The last-entry assertion moved to tests/unit/storage/test_migration_154.py."""
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


async def test_every_stored_map_gets_its_groups_and_keeps_its_areas(fresh_db: DatabasePool) -> None:
    assert _sql(), f"there is no migration {VERSION}: a map stored before #180 serves no groups"
    await _migrate(fresh_db, range(1, VERSION))
    current = json.dumps(
        parse_map(
            {"format": FORMAT, "groups": GROUPS, "nodes": [{"id": "a", "group": "ops", "label": "A"}]}, "new"
        ).graph,
        separators=(",", ":"),
    )
    stored = {"shop": _old_graph("shop"), "org": NOT_JSONB, "x": NO_AREAS, "new": current}
    for slug, graph in stored.items():
        await fresh_db.execute("INSERT INTO knowledge_maps (slug, title, graph) VALUES ($1, $1, $2)", slug, graph)

    await _migrate(fresh_db, range(VERSION, VERSION + 1))

    after = {r["slug"]: r["graph"] for r in await fresh_db.fetch("SELECT slug, graph FROM knowledge_maps")}
    shop = json.loads(after["shop"])
    assert shop["groups"] == shop["areas"] == json.loads(json.dumps(GROUPS)), (
        f"a stored map must serve its areas as groups, and keep areas: {after['shop'][:400]}"
    )
    assert [(n.get("group"), n["area"]) for n in shop["nodes"]] == [('ops "x",\\ "label":',) * 2, (None, None)], (
        f"each stored part must serve its ring as group, and keep area: {shop['nodes']}"
    )
    shop["nodes"] = [{k: v for k, v in node.items() if k != "group"} for node in shop["nodes"]]
    assert {k: v for k, v in shop.items() if k != "groups"} == json.loads(stored["shop"]), "nothing else may change"
    org = json.loads(after["org"])
    assert org["groups"] == org["areas"] and org["groups"][0]["id"] == "g", (
        f"a graph JSONB refuses must get its groups too, by text: {after['org']}"
    )
    assert after["x"] == NO_AREAS, "a graph with no areas list is left as it was"
    assert after["new"] == current, "a graph that has its groups already is left as it was"


#: A part's age and use hold keys the scanner chose: keys of a part, of a stored part, and of a stored graph.
OWN_KEYS = [
    {"id": "a", "area": "ops", "use": {"z": 1, "area": "spoof", "label": "y"}},
    {"id": "b", "area": "ops", "use": {"id": "x", "kind": "k", "area": "spoof", "label": "y"}},
    {"id": "c", "area": None, "age": {"group": "q", "groups": 2, "areas": 3}},
    {"id": "d", "area": "ops"},
]


async def test_a_parts_own_keys_neither_take_a_group_nor_stop_one(fresh_db: DatabasePool) -> None:
    """#659 review: a part's age and use are dicts whose keys the scanner chooses. Their area was copied as a group,
    and a group or groups key in any of them kept every part, and the graph's list, from being copied."""
    await _migrate(fresh_db, range(1, VERSION))
    stored = _old_graph("keys", OWN_KEYS)
    await fresh_db.execute("INSERT INTO knowledge_maps (slug, title, graph) VALUES ($1, $1, $2)", "keys", stored)

    await _migrate(fresh_db, range(VERSION, VERSION + 1))

    graph = await fresh_db.fetchval("SELECT graph FROM knowledge_maps WHERE slug = 'keys'")
    after = json.loads(graph)
    assert [n.get("use") for n in after["nodes"][:2]] == [n["use"] for n in OWN_KEYS[:2]], (
        f"a key a scanner chose took a group: {[n.get('use') for n in after['nodes']]}"
    )
    assert [n.get("group", "missing") for n in after["nodes"]] == ["ops", "ops", None, "ops"], (
        f"every part gets its group, whatever keys another part's age holds: {after['nodes']}"
    )
    assert after.get("groups") == after["areas"], (
        f"a groups key in a part's age kept the graph's own list from being copied: {graph[:300]}"
    )
    await fresh_db.execute(_sql())
    again = await fresh_db.fetchval("SELECT graph FROM knowledge_maps WHERE slug = 'keys'")
    assert again == graph, "a graph migrated already is left as it is"
