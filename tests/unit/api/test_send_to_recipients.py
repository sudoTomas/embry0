"""A decision sent to someone it cannot go to answers alike, whoever they are or are not.

``POST /decisions/{id}/send-to`` sends a decision to a person who signs in or is named by a GitHub login. A
recipient nobody knows, one who is disabled and one who may not be sent decisions (no role, no login) answer the
same 422, status, headers and body, write nothing and leave no audit row, so the answer tells the caller nothing
about who exists. On the real app over a real database (:mod:`tests.fixtures.repository_writes`), through a key
with a repository allowlist and one without.
"""

from __future__ import annotations

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

#: Recipients a decision cannot go to, by why.
REFUSED = {
    "nobody knows them": "nobody@example.com",
    "disabled": "gone@example.com",
    "disabled, spelled otherwise": "GONE@Example.com",
    "no role and no login": "visitor@example.com",
}


async def test_every_recipient_a_decision_cannot_go_to_answers_alike(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, values):
        await db.execute(
            "INSERT INTO users (id, auth0_sub, email, name, tier, disabled) VALUES "
            "('usr-gone', 'auth0|gone', 'gone@example.com', 'Gone', 'operator', true), "
            "('usr-visitor', 'auth0|visitor', 'visitor@example.com', 'Visitor', 'none', false), "
            "('usr-ada', 'auth0|ada', 'ada@example.com', 'Ada', 'operator', false)"
        )
        url = f"/api/v1/decisions/{values[COVERED]['decision_id']}/send-to"
        before = await db.fetchval("SELECT count(*) FROM audit_log"), await db.fetchval("SELECT count(*) FROM handoffs")
        answers = {
            (key, why): await call(app, "POST", url, getattr(keys, key), {"to": to, "note": "please"})
            for key in ("limited", "open")
            for why, to in REFUSED.items()
        }
        after = await db.fetchval("SELECT count(*) FROM audit_log"), await db.fetchval("SELECT count(*) FROM handoffs")
        sent = await call(app, "POST", url, keys.limited, {"to": "ada@example.com", "note": "please"})
    shapes = {name: (r.status_code, dict(r.headers), r.content) for name, r in answers.items()}
    first = next(iter(shapes.values()))
    differ = {name: shape for name, shape in shapes.items() if shape != first}
    assert first[0] == 422 and differ == {}, f"recipients it cannot go to answer apart: {first} against {differ}"
    assert after == before, f"a refused send wrote audit rows or hand-offs: {before} -> {after}"
    assert sent.status_code == 201, f"a person who signs in is sent it: {sent.text}"
