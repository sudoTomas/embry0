"""A job steered from a chat (``POST /asks/jobs/{id}/answer``, ``/resume``, ``/stop``) is held to who may steer it,
on the real app over a real database: whoever started it, or an admin, and never a disabled user."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, NOW, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

JOB = "job-steered-1"


async def _started_by_ada(db: DatabasePool) -> None:
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, dispatched_by) "
        "VALUES ($1, $2, 'Fix it', 'running', 'full-magnum-opus', $3, $3, 'ada@example.com')",
        JOB,
        COVERED,
        NOW - timedelta(minutes=5),
    )
    await db.execute(
        "INSERT INTO users (id, auth0_sub, email, name, tier) VALUES ('usr-ada', 'usr-ada', 'ada@example.com', 'Ada', "
        "'operator'), ('usr-root', 'usr-root', 'root@example.com', 'Root', 'admin')"
    )


async def _stop(app: Any, token: str, person: str) -> Any:
    return await call(app, "POST", f"/api/v1/asks/jobs/{JOB}/stop", token, {"person": person})


async def test_a_disabled_user_steers_no_job_from_a_chat(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _started_by_ada(db)
        await db.execute("UPDATE users SET disabled = true")
        refused = {who: (await _stop(app, keys.open, f"{who}@example.com")).status_code for who in ("ada", "root")}
    assert refused == {"ada": 403, "root": 403}, (
        f"a disabled user steered a job from a chat, as the one who started it or as an admin: {refused}"
    )


#: Each case: the people rows (id, email, linked user, disabled) and how the caller is found among them.
PEOPLE: dict[str, list[tuple[str, str | None, str | None, bool]]] = {
    "by email": [("per-ada", "ada@example.com", None, True)],
    "by sign-in": [("per-ada", None, "usr-ada", True)],
    "as one of the two they are": [
        ("per-ada", "ada@example.com", None, False),
        ("per-ada-work", "ada.work@example.com", "usr-ada", True),
    ],
}


@pytest.mark.parametrize("found", list(PEOPLE))
async def test_a_disabled_person_steers_no_job_from_a_chat(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, found: str
) -> None:
    """The person may be disabled while their user is not: the person is refused all the same, whether the caller is
    that person by their email or by their sign-in, and when they are two people, one of them disabled."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _started_by_ada(db)
        for row in PEOPLE[found]:
            await db.execute(
                "INSERT INTO people (id, name, email, user_id, disabled) VALUES ($1, 'Ada', $2, $3, $4)", *row
            )
        stopped = await _stop(app, keys.open, "ada@example.com")
    assert stopped.status_code == 403, (
        f"a disabled person, found {found}, stopped the job they started from a chat: "
        f"{stopped.status_code} {stopped.text}"
    )


async def test_the_one_who_started_a_job_still_steers_it(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _started_by_ada(db)
        stopped = await _stop(app, keys.open, "ada@example.com")
    assert stopped.status_code != 403, f"the one who started the job was refused: {stopped.status_code} {stopped.text}"
