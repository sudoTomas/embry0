"""What a key with a repository allowlist reads of its repositories' work, on the real app over a real database.

A list that pages in SQL is narrowed to the key's repositories before its
limit, so the key's own work is on its page however much newer work a
repository it does not cover has. The progression pages, ``GET /progression``
and ``GET /progression/preview``, show an achievement's evidence and a
quest's place only when they name a repository the key covers.

The app and its keys are :mod:`tests.fixtures.repository_writes`'.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, MASTER, NOW, UNCOVERED, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres

#: How many newer jobs the repository the key does not cover has: more than any of the pages hold.
NEWER = 70


async def _newer_work(db: DatabasePool) -> None:
    """``NEWER`` finished jobs with pull requests and ``NEWER`` live ones in the uncovered repository, all newer
    than the covered repository's, each finished one run by the developer."""
    for i in range(NEWER):
        job = f"job-newer-{i:03d}"
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, finished_at, "
            "pr_url, delivery_state, delivery_state_at, dispatched_by) VALUES ($1, $2, 'Newer', 'completed', "
            "'full-magnum-opus', $3, $3, $4, $5, 'pr_open', $4, 'ada@example.com')",
            job,
            UNCOVERED,
            NOW - timedelta(minutes=50 - i * 0.5),
            NOW - timedelta(minutes=40 - i * 0.5),
            f"https://github.com/{UNCOVERED}/pull/{100 + i}",
        )
        await db.execute(
            "INSERT INTO traces (trace_id, job_id, agent_type, model, result, cost_usd, duration_ms, created_at) "
            "VALUES ($1, $2, 'developer', 'm', 'success', 1, 1000, $3)",
            f"tr-{job}",
            job,
            NOW - timedelta(minutes=40 - i * 0.5),
        )
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, dispatched_by) "
            "VALUES ($1, $2, 'Live', 'running', 'full-magnum-opus', $3, $3, 'ada@example.com')",
            f"job-newer-live-{i:03d}",
            UNCOVERED,
            NOW - timedelta(seconds=30 - i * 0.1),
        )


async def test_a_key_s_own_work_is_on_its_page_however_much_newer_work_another_repository_has(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, values):
        finished = values[COVERED]["job_id"]
        await db.execute(
            "INSERT INTO traces (trace_id, job_id, agent_type, model, result, cost_usd, duration_ms, created_at) "
            "VALUES ('tr-own', $1, 'developer', 'm', 'success', 1, 1000, $2)",
            finished,
            NOW - timedelta(hours=1),
        )
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, dispatched_by) "
            "VALUES ('job-own-live', $1, 'Own live', 'running', 'full-magnum-opus', $2, $2, 'ada@example.com')",
            COVERED,
            NOW - timedelta(minutes=10),
        )
        await _newer_work(db)
        world = (await call(app, "GET", "/api/v1/world", keys.limited)).json()
        review = (await call(app, "GET", "/api/v1/decisions?stage=done", keys.limited)).json()
        character = (await call(app, "GET", "/api/v1/world/characters/developer", keys.limited)).json()
        everyone = (await call(app, "GET", "/api/v1/world", keys.open)).json()

    def jobs(rows: list[dict[str, Any]]) -> list[str]:
        return [str(r["job_id"]) for r in rows]

    assert finished not in jobs(everyone["inbox"]), "the newer jobs fill the inbox's page: the cut is real"
    seen = {
        "the inbox": jobs(world["inbox"]),
        "the loot": jobs(world["loot"]),
        "the parties": jobs(world["parties"]),
        "the developer's quest log": jobs(character["quest_log"]),
        "the quests to review": [d["id"] for d in review["decisions"]],
    }
    assert seen == {
        "the inbox": [finished],
        "the loot": [finished],
        "the parties": ["job-own-live"],
        "the developer's quest log": [finished],
        "the quests to review": [f"done:{finished}"],
    }, f"the key's own work, and only it, is on each page: {seen}"


async def _progress(db: DatabasePool) -> None:
    """An onboarding in the uncovered repository followed by a merged pull request there, the newest merge; and an
    owner's answer in the covered one."""
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, finished_at) "
        "VALUES ('job-onboard', $1, 'Onboard', 'completed', 'onboard', $2, $2, $3)",
        UNCOVERED,
        NOW - timedelta(days=2),
        NOW - timedelta(days=2) + timedelta(minutes=5),
    )
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, finished_at, "
        "pr_url, delivery_state, delivery_state_at, dispatched_by) VALUES ('job-merged', $1, 'Merged', 'completed', "
        "'full-magnum-opus', $2, $2, $3, $4, 'merged', $5, 'ada@example.com')",
        UNCOVERED,
        NOW - timedelta(hours=3),
        NOW - timedelta(hours=2),
        f"https://github.com/{UNCOVERED}/pull/7",
        NOW - timedelta(hours=1),
    )
    await db.execute(
        "INSERT INTO proposal_decisions (proposal_id, repo, kind, decision, decided_by) "
        "VALUES ('finding:old', $1, 'finding', 'decline', 'ada')",
        COVERED,
    )


def _shown(body: dict[str, Any]) -> dict[str, Any]:
    evidence = {a["id"]: (a["evidence"] or {}).get("label") for a in body["achievements"] if a["tier"]}
    return {
        "forged_in_fire": evidence.get("forged_in_fire"),
        "talked_to_the_quest_giver": evidence.get("talked_to_the_quest_giver"),
        "first_steps": next(q["where"] for q in body["quests"] if q["id"] == "first_steps"),
        "diaries": sorted(d["repo"] for d in body["diaries"]),
    }


@pytest.mark.parametrize("route", ["/api/v1/progression", "/api/v1/progression/preview"])
async def test_the_progression_pages_name_only_the_key_s_repositories(
    route: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The newest merge, the evidence of the merge achievements, is the uncovered repository's, and so is the
    onboarding quest's place: the limited key gets neither, and keeps its own repository's evidence."""
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, keys, _values):
        await _progress(db)
        if route.endswith("/preview"):
            text = (await call(app, "GET", "/api/v1/progression/pack", MASTER)).json()["text"]
            saved = await call(app, "PUT", "/api/v1/progression/pack/draft", MASTER, text + "\n# a comment\n")
            assert saved.status_code == 200, saved.text
        limited = await call(app, "GET", route, keys.limited)
        everyone = await call(app, "GET", route, keys.open)
    assert limited.status_code == 200 and everyone.status_code == 200, (limited.text[:300], everyone.text[:300])
    assert _shown(everyone.json()) == {
        "forged_in_fire": f"{UNCOVERED} #7",
        "talked_to_the_quest_giver": f"{COVERED} · decline",
        "first_steps": UNCOVERED,
        "diaries": sorted([COVERED, UNCOVERED]),
    }, f"a key without an allowlist reads every repository's: {_shown(everyone.json())}"
    assert _shown(limited.json()) == {
        "forged_in_fire": None,
        "talked_to_the_quest_giver": f"{COVERED} · decline",
        "first_steps": None,
        "diaries": [COVERED],
    }, f"{route} names to the limited key its own repository and no other: {_shown(limited.json())}"
    assert UNCOVERED not in limited.text, f"{route} names {UNCOVERED} nowhere in its answer to the limited key"
