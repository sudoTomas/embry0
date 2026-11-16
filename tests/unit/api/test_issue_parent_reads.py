"""An issue whose parent a key with a repository allowlist does not read reads, to that key, as an orphan.

A child keeps its issue when its parent is deleted: the reference is set null,
and the child is one of the top-level issues from then on. A key covering only
``acme/web`` reads an issue of ``acme/web`` whose parent is ``globex/gizmos``'
the same way: at the top level, its ``parent_issue_id`` null, in the default
list, under ``?parent_issue_id=any`` and in its own detail. A child whose
parent the key reads stays below that parent.

On the real app over a real database (:mod:`tests.fixtures.repository_writes`).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI

from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, UNCOVERED, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

#: The issues seeded here: each one's repository, and its parent before any is deleted.
ISSUES: dict[str, tuple[str, str | None]] = {
    "elsewhere-parent": (UNCOVERED, None),
    "child-of-elsewhere": (COVERED, "elsewhere-parent"),
    "deleted-parent": (COVERED, None),
    "child-of-deleted": (COVERED, "deleted-parent"),
    "own-parent": (COVERED, None),
    "child-of-own": (COVERED, "own-parent"),
}
#: What the key reads of an issue's place among the others: its parent and what it counts below it.
PLACE = ("parent_issue_id", "children_count", "children_closed_count")


async def _seed(db: DatabasePool) -> None:
    for issue_id, (repo, _parent) in ISSUES.items():
        await db.execute(
            "INSERT INTO issues (id, title, repo, status) VALUES ($1, $2, $3, 'open')", issue_id, issue_id, repo
        )
    for issue_id, (_repo, parent) in ISSUES.items():
        if parent:
            await db.execute("UPDATE issues SET parent_issue_id = $2 WHERE id = $1", issue_id, parent)
    await db.execute("DELETE FROM issues WHERE id = 'deleted-parent'")
    assert await db.fetchval("SELECT parent_issue_id FROM issues WHERE id = 'child-of-deleted'") is None


async def _listed(app: FastAPI, token: str, query: str = "") -> dict[str, dict[str, Any]]:
    response = await call(app, "GET", f"/api/v1/issues?limit=200{query}", token)
    assert response.status_code == 200, response.text
    body = response.json()
    rows = {row["id"]: row for row in body["issues"] if row["id"] in ISSUES}
    assert body["total"] == len(body["issues"]), "the total counts other rows than the page holds"
    return rows


async def test_a_child_of_a_parent_the_key_does_not_read_reads_as_a_child_of_a_deleted_one(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _seed(db)
        top = await _listed(app, keys.limited)
        anywhere = await _listed(app, keys.limited, "&parent_issue_id=any")
        under_own = await _listed(app, keys.limited, "&parent_issue_id=own-parent")
        detail = {}
        for issue_id in ("child-of-elsewhere", "child-of-deleted"):
            response = await call(app, "GET", f"/api/v1/issues/{issue_id}", keys.limited)
            assert response.status_code == 200, response.text
            detail[issue_id] = response.json()
        open_top = await _listed(app, keys.open)

    # The walk measures: a key without a list reads the parent, so the child is below it, not at the top.
    assert "child-of-elsewhere" not in open_top and "elsewhere-parent" in open_top, sorted(open_top)
    assert sorted(top) == ["child-of-deleted", "child-of-elsewhere", "own-parent"], (
        f"the top-level issues a key covering only {COVERED} reads: {sorted(top)}"
    )
    assert sorted(anywhere) == ["child-of-deleted", "child-of-elsewhere", "child-of-own", "own-parent"], sorted(
        anywhere
    )
    assert sorted(under_own) == ["child-of-own"], sorted(under_own)
    for where, rows in (("the top level", top), ("?parent_issue_id=any", anywhere)):
        hidden = {field: rows["child-of-elsewhere"][field] for field in PLACE}
        orphan = {field: rows["child-of-deleted"][field] for field in PLACE}
        assert hidden == orphan, f"{where}: a child of a parent the key does not read {hidden}, an orphan {orphan}"
    hidden = {field: detail["child-of-elsewhere"].get(field) for field in PLACE}
    orphan = {field: detail["child-of-deleted"].get(field) for field in PLACE}
    assert hidden == orphan, f"the detail: a child of a parent the key does not read {hidden}, an orphan {orphan}"
    assert anywhere["child-of-own"]["parent_issue_id"] == "own-parent", "a parent the key reads stays named"
