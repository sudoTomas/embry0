"""``GET /issues``'s ``labels`` and ``parent_issue_id`` filters, on the real app over a real database.

``labels`` keeps the issues carrying that one label (#350: the label was sent
as a string of JSON, which the pool's jsonb codec encoded again, so no issue
ever matched). ``parent_issue_id`` names the issue whose children are listed
(#351: any value but the default listed every issue); ``any`` lists every
issue at any level, and a parent the key does not read lists as one that
does not exist.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import quote

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.issue_reads import (
    CHILD,
    CHILD_ELSEWHERE,
    CHILD_HERE,
    ISSUE,
    MIXED,
    MIXED_HIDDEN,
    issue_reads_app,
    same_answer,
)
from tests.fixtures.repository_writes import COVERED, NOW, UNCOVERED, call

pytestmark = pytest.mark.requires_postgres

#: A parent id that names no issue, as long as the hidden parent's.
NO_PARENT = "iss-zzzzz-zzzzz"
assert len(NO_PARENT) == len(MIXED_HIDDEN), "the missing parent's id is as long as the hidden one's"

#: The labelled issues, each with its labels: one label alone, shared, with a quote and a backslash, and one that
#: only starts like another.
LABELLED = {
    "iss-label-bug": ["bug"],
    "iss-label-bug-ui": ["bug", "ui"],
    "iss-label-quoted": ['say "hi"', "back\\slash"],
    "iss-label-prefix": ["bugfix"],
}


async def _labelled(db: DatabasePool) -> None:
    for n, (issue_id, labels) in enumerate(LABELLED.items()):
        await db.execute(
            "INSERT INTO issues (id, title, body, labels, repo, status, created_at) "
            "VALUES ($1, $1, '', $2::jsonb, $3, 'open', $4)",
            issue_id,
            labels,
            COVERED,
            NOW - timedelta(minutes=n),
        )


async def _listed(app: Any, token: str, query: str) -> tuple[set[str], int]:
    response = await call(app, "GET", f"/api/v1/issues?limit=200{query}", token)
    assert response.status_code == 200, f"{query}: {response.text}"
    body = response.json()
    return {i["id"] for i in body["issues"]}, body["total"]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("bug", {"iss-label-bug", "iss-label-bug-ui"}),
        ("ui", {"iss-label-bug-ui"}),
        ('say "hi"', {"iss-label-quoted"}),
        ("back\\slash", {"iss-label-quoted"}),
        ("bugfix", {"iss-label-prefix"}),
        ("nothing-carries-this", set()),
    ],
    ids=["shared", "second-label", "quote", "backslash", "longer-label", "none"],
)
async def test_the_labels_filter_lists_the_issues_carrying_that_label(
    label: str, expected: set[str], db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        await _labelled(db_with_migrations)
        listed, total = await _listed(app, keys.open, f"&parent_issue_id=any&labels={quote(label, safe='')}")
    assert (listed, total) == (expected, len(expected)), (
        f"?labels={label!r} lists the issues carrying that label, and counts them: {sorted(listed)}, total {total}"
    )


async def test_the_parent_filter_lists_that_issue_s_children(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        children = {
            parent: await _listed(app, keys.open, f"&parent_issue_id={parent}")
            for parent in (ISSUE[COVERED], ISSUE[UNCOVERED], MIXED, MIXED_HIDDEN, CHILD[COVERED], NO_PARENT)
        }
        every, every_total = await _listed(app, keys.open, "&parent_issue_id=any")
        top, _top_total = await _listed(app, keys.open, "")
    assert children == {
        ISSUE[COVERED]: ({CHILD[COVERED]}, 1),
        ISSUE[UNCOVERED]: ({CHILD[UNCOVERED]}, 1),
        MIXED: ({CHILD_ELSEWHERE}, 1),
        MIXED_HIDDEN: ({CHILD_HERE}, 1),
        CHILD[COVERED]: (set(), 0),
        NO_PARENT: (set(), 0),
    }, f"?parent_issue_id=<id> lists that issue's children alone, and counts them: {children}"
    kids = {CHILD[COVERED], CHILD[UNCOVERED], CHILD_ELSEWHERE, CHILD_HERE}
    assert kids <= every and every_total == len(every), f"?parent_issue_id=any lists every issue: {sorted(every)}"
    assert not kids & top and top, f"the default lists the top-level issues: {sorted(top)}"


async def test_a_parent_the_key_does_not_read_has_no_children_it_lists(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIXED_HIDDEN is the other repository's; its child CHILD_HERE is the key's, but names no parent to the key, so
    listing the children of MIXED_HIDDEN answers as for a parent that does not exist. Its own parents list their
    children of its repositories."""
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        hidden = await call(app, "GET", f"/api/v1/issues?parent_issue_id={MIXED_HIDDEN}", keys.limited)
        missing = await call(app, "GET", f"/api/v1/issues?parent_issue_id={NO_PARENT}", keys.limited)
        own = await _listed(app, keys.limited, f"&parent_issue_id={ISSUE[COVERED]}")
        mixed = await _listed(app, keys.limited, f"&parent_issue_id={MIXED}")
        everyone = await _listed(app, keys.open, f"&parent_issue_id={MIXED_HIDDEN}")
    assert everyone == ({CHILD_HERE}, 1), f"a key without a list reads the child, so the check measures: {everyone}"
    differs = same_answer(hidden, {}, missing, {})
    assert differs is None, f"a parent the key does not read lists as one that does not exist: {differs}"
    assert own == ({CHILD[COVERED]}, 1), f"the key lists its own issue's children: {own}"
    assert mixed == (set(), 0), f"the key lists no child of a repository it does not cover: {mixed}"
