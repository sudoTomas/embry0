"""A finished quest answered from a chat is held to its owners, on the real app over a real database.

The feed lists a quest to review with its owners (its area's, else its
repository's, else CODEOWNERS'). An answer from a chat reads the decision
again by its id and decides who may answer on what it reads, so that read
must carry the same owners: an owner may answer, and someone else may not.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.asks import AsksRepository
from tests.fixtures.repository_writes import COVERED, MASTER, NOW, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

JOB = "job-answered-done-1"
DECISION = f"done:{JOB}"


async def _finished_quest(app: Any, db: DatabasePool) -> str:
    """A quest to review in the covered repository, owned by ada, a queued ask about it, and asks on; the ask's
    code."""
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, finished_at, "
        "pr_url, delivery_state, dispatched_by) VALUES ($1, $2, 'Fix it', 'completed', 'full-magnum-opus', $3, $3, "
        "$4, $5, 'pr_open', 'ada@example.com')",
        JOB,
        COVERED,
        NOW - timedelta(minutes=30),
        NOW - timedelta(minutes=10),
        f"https://github.com/{COVERED}/pull/7",
    )
    for user_id, email, login in (("usr-ada", "ada@example.com", "ada"), ("usr-bo", "bo@example.com", "bo")):
        await db.execute(
            "INSERT INTO users (id, auth0_sub, email, name, tier, github_login) VALUES ($1, $1, $2, $3, 'operator', $3)",
            user_id,
            email,
            login,
        )
    await app.state.repo_preferences_repo.upsert(COVERED, owners=["ada"])
    settings = {"asks": {"enabled": True, "unowned_group": "triage", "hours": "00-24", "weekdays": "mon-sun"}}
    put = await call(app, "PUT", "/api/v1/config/settings", MASTER, settings)
    assert put.status_code == 200, put.text
    await db.execute(
        "INSERT INTO asks (id, decision_id, recipient, state, due_at, created_at) "
        "VALUES ('ask-done-1', $1, 'ada@example.com', 'queued', $2, $2)",
        DECISION,
        NOW - timedelta(minutes=5),
    )
    return await AsksRepository(db).code(DECISION)


async def test_a_finished_quest_read_by_its_id_carries_its_owners(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _finished_quest(app, db)
        read = await call(app, "GET", f"/api/v1/decisions/{DECISION}", keys.open)
    assert read.status_code == 200, read.text
    assert read.json()["owners"] == ["ada"], (
        f"a finished quest read by its id has no owners, so who may answer it is decided on none: {read.json()}"
    )


async def test_only_a_finished_quest_s_owner_answers_it_from_a_chat(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        code = await _finished_quest(app, db)

        async def answer(person: str, to: str) -> Any:
            body = {"code": code, "action": "send_to", "person": person, "to": to, "note": "yours"}
            return await call(app, "POST", "/api/v1/asks/answer", keys.open, body)

        other = await answer("bo@example.com", "ada@example.com")
        owner = await answer("ada@example.com", "bo@example.com")
    assert other.status_code == 403, (
        f"an answer from a chat was not held to the quest's owners: {other.status_code} {other.text}"
    )
    assert owner.status_code == 200, f"the quest's owner could not answer it: {owner.status_code} {owner.text}"
