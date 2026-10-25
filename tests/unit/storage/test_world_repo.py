"""The world's rows (spec 2026-10-25): parties, quest logs, totals, the inbox and the player's ratings, on Postgres."""

from __future__ import annotations

import json

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.world import WorldRepository

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def _seed(db: DatabasePool) -> None:
    await db.execute(
        """
        INSERT INTO jobs (job_id, status, repo, task, pr_url, delivery_state, created_at, finished_at, dispatched_by, total_cost_usd) VALUES
          ('job-live', 'running', 'acme/web', 'Fix it', NULL, 'none', NOW() - INTERVAL '10 minutes', NULL, 'octo', 0),
          ('job-done', 'completed', 'acme/web', 'Add it', 'https://github.com/acme/web/pull/7', 'pr_open',
              NOW() - INTERVAL '2 hours', NOW() - INTERVAL '1 hour', 'octo', 2.5),
          ('job-tick', 'completed', 'acme/web', 'Watch logs', NULL, 'none',
              NOW() - INTERVAL '2 hours', NOW() - INTERVAL '1 hour', 'embry0:watcher', 0.2),
          ('job-old', 'pr_merged', 'acme/api', 'Old fix', 'https://github.com/acme/api/pull/1', 'merged',
              NOW() - INTERVAL '40 days', NOW() - INTERVAL '40 days', 'octo', 1.0)
        """
    )
    await db.execute(
        """
        INSERT INTO traces (trace_id, job_id, agent_type, model, cost_usd, duration_ms, result) VALUES
          ('t1', 'job-done', 'developer', 'm', 2.0, 60000, 'success'),
          ('t2', 'job-done', 'review', 'm', 0.5, 30000, 'success'),
          ('t3', 'job-old', 'developer', 'm', 1.0, 20000, 'success'),
          ('t4', 'job-tick', 'analysis', 'm', 0.2, 5000, 'success')
        """
    )
    for event, agent in (("agent_started", "triage"), ("agent_completed", "triage"), ("agent_started", "developer")):
        await db.execute(
            "INSERT INTO job_logs (job_id, text, event_type) VALUES ('job-live', $1, $2)",
            json.dumps({"type": event, "agent": agent}),
            event,
        )
    await db.execute("INSERT INTO job_logs (job_id, text, event_type) VALUES ('job-live', 'plain output', 'text')")
    await db.execute(
        "INSERT INTO proposal_decisions (proposal_id, repo, kind, decision, decided_by, job_id) VALUES ('case:3', 'acme/web', 'case', 'accept', 'octo', 'job-live')"
    )


async def test_parties_their_runs_and_the_lead_they_came_from(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _seed(db)
    repo = WorldRepository(db)
    jobs = {j["job_id"] for j in await repo.jobs(returning_seconds=60)}
    assert jobs == {"job-live"}, "a job back an hour ago is no longer walking home"
    assert (await repo.agent_runs(["job-live"]))["job-live"] == [
        ("agent_started", "triage"),
        ("agent_completed", "triage"),
        ("agent_started", "developer"),
    ]
    assert await repo.lead_links(["job-live", "job-done"]) == {"job-live": "case:3"}
    assert set(await repo.last_event_at(["job-live"])) == {"job-live"}


async def test_a_character_s_quest_log_and_totals(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _seed(db)
    repo = WorldRepository(db)
    log = await repo.quest_log("developer")
    assert [q["job_id"] for q in log] == ["job-done", "job-old"]
    assert float(log[0]["cost_usd"]) == 2.0 and log[0]["rating"] is None
    stats = (await repo.stats("developer"))["developer"]
    assert (stats["quests"], stats["succeeded"], stats["merged"]) == (2, 2, 1)
    assert set(await repo.stats()) == {"developer", "review", "analysis"}


async def test_the_inbox_holds_the_player_s_finished_quests_until_they_are_evaluated(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    await _seed(db)
    repo = WorldRepository(db)
    inbox = await repo.inbox(days=14, player_actors=["embry0:master-key"])
    assert [q["job_id"] for q in inbox] == ["job-done"], (
        "a watcher tick is no quest of the player's; an old one is past"
    )
    assert inbox[0]["agent_type"] == "developer", "drawn as the character that did the most of it"
    first = await repo.rate("job-done", rated_by="octo", rating="good", note="clean")
    again = await repo.rate("job-done", rated_by="octo", verdict="merged")
    assert (first["rating"], again["rating"], again["verdict"], again["note"]) == ("good", "good", "merged", "clean")
    assert await repo.inbox(days=14, player_actors=[]) == []
    assert (await repo.quest_log("developer"))[0]["verdict"] == "merged"
