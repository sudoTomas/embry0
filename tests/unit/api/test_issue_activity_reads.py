"""``GET /issues/{id}/activity`` reads an issue's audit rows as ``GET /audit`` reads rows.

On the real app over a real database (:mod:`tests.fixtures.repository_writes`):
an issue of ``acme/web`` carries rows by two people, one of embry0's own
actors, a reader key's own, a refusal's, one that also names ``globex/gizmos``
(which the limited key does not cover), and one whose details hold a secret
inside a list. Three callers read it:

- an admin key reads every row and every actor;
- the limited key (admin scope, covering ``acme/web``) reads only the rows about
  the repositories it covers, and no refusal's (:mod:`embry0.api.audit_allowlist`);
- a read key reads every row and who acted on each: an issue's activity is a
  collaboration view, so its actors are not held to the change-history rule
  (:mod:`embry0.api.actor_visibility`).

Every reader gets the details redacted, and no ``trace_id``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from embry0.api.deps import bust_api_key_cache
from embry0.orchestration.dispatch_actors import MASTER_KEY, REPO_ALLOWLIST
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from tests.fixtures.repository_writes import COVERED, UNCOVERED, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

PERSON, OTHER_PERSON, READER = "sam@example.com", "kim@example.com", "key:activity-reader"
SECRET = "do-not-show-this-value"


async def _row(db: DatabasePool, action: str, actor: str, details: Mapping[str, Any], issue_id: str) -> int:
    return int(
        await db.fetchval(
            "INSERT INTO audit_log (action, actor, details, issue_id, trace_id) "
            "VALUES ($1, $2, $3, $4, 'trc-internal') RETURNING id",
            action,
            actor,
            dict(details),
            issue_id,
        )
    )


async def test_an_issue_s_activity_is_read_as_the_audit_log_is_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, values):
        _row_, reader = await ApiKeysRepository(db).create(
            name="activity-reader", scope="read", user_id=None, created_by="tests"
        )
        bust_api_key_cache()
        issue = values[COVERED]["issue_id"]
        rows = {
            "created": await _row(db, "issue.created", PERSON, {"repo": COVERED}, issue),
            "triaged": await _row(db, "issue.triaged", OTHER_PERSON, {}, issue),
            "by embry0": await _row(db, "issue.status_changed", MASTER_KEY, {}, issue),
            "the reader's": await _row(db, "issue.updated", READER, {}, issue),
            "a refusal": await _row(db, "issue.updated", REPO_ALLOWLIST, {"repo": COVERED}, issue),
            "beside another repository": await _row(db, "case_accepted", PERSON, {"repo": UNCOVERED}, issue),
            "a secret in a list": await _row(
                db, "issue.updated", PERSON, {"updates": [{"api_key": SECRET, "title": "t"}]}, issue
            ),
        }
        # Another issue's row, of a repository every caller here reads: not this issue's activity.
        await _row(db, "issue.created", PERSON, {"repo": COVERED}, "another-issue")
        url = f"/api/v1/issues/{issue}/activity"
        read = {
            name: await call(app, "GET", url, token)
            for name, token in (("admin", keys.open), ("limited", keys.limited), ("reader", reader))
        }

    for name, response in read.items():
        assert response.status_code == 200, f"{name}: {response.status_code} {response.text}"
        assert SECRET not in response.text, f"{name} reads a secret in an issue's activity"
        assert all("trace_id" not in entry for entry in response.json()), f"{name} reads a trace id"
    by_id = {name: {e["id"]: e for e in response.json()} for name, response in read.items()}
    every = {rows[name] for name in rows}

    assert set(by_id["admin"]) == every
    assert {by_id["admin"][rows[name]]["actor"] for name in ("created", "triaged")} == {PERSON, OTHER_PERSON}
    assert by_id["admin"][rows["a secret in a list"]]["details"] == {
        "updates": [{"api_key": "[redacted]", "title": "t"}]
    }

    hidden = {rows["a refusal"], rows["beside another repository"]}
    assert set(by_id["limited"]) == every - hidden, "the limited key reads rows past its repositories, or a refusal's"

    actors = {
        rows["created"]: PERSON,
        rows["triaged"]: OTHER_PERSON,
        rows["by embry0"]: MASTER_KEY,
        rows["the reader's"]: READER,
        rows["a refusal"]: REPO_ALLOWLIST,
        rows["beside another repository"]: PERSON,
        rows["a secret in a list"]: PERSON,
    }
    reader_sees = {e_id: entry["actor"] for e_id, entry in by_id["reader"].items()}
    assert reader_sees == actors, "a reader of the issue does not see who acted on it"
    limited_sees = {e_id: entry["actor"] for e_id, entry in by_id["limited"].items()}
    assert limited_sees == {e_id: a for e_id, a in actors.items() if e_id not in hidden}


async def test_another_repository_s_issue_activity_reads_as_missing(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, values):
        issue = values[UNCOVERED]["issue_id"]
        await _row(db, "issue.created", PERSON, {"repo": UNCOVERED}, issue)
        response = await call(app, "GET", f"/api/v1/issues/{issue}/activity", keys.limited)
    assert response.status_code == 404, response.text
