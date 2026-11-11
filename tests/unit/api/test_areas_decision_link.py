"""A map of a repository's areas, once done, links its card to where its areas wait for a person (spec 2026-11-10).

The review inbox's card of a map job carries ``links.areas_map``, the proposal its job's pipeline config names,
which the dashboard turns into a link to the repository's Areas section, while that map, its job's own, waits for
a person. Read from the job's own record, not from
the name of its template. On the real app over a real database (:mod:`tests.fixtures.repository_writes`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, MASTER, call, repository_writes_app

pytestmark = pytest.mark.requires_postgres


async def test_a_done_map_s_card_links_its_waiting_areas_and_no_other_card_does(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    now = datetime.now(UTC)
    async with repository_writes_app(db, monkeypatch) as (app, _keys, values):
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, pipeline_config, created_at, started_at, "
            "finished_at, dispatched_by) VALUES ('job-map', $1, 'Map the areas of acme/web', 'completed', 'map-areas', "
            "$2::jsonb, $3, $3, $4, 'ada@example.com')",
            COVERED,
            {"areas_proposal_id": "areas-widget", "max_budget_usd": 2.0},
            now - timedelta(minutes=30),
            now - timedelta(minutes=10),
        )
        await db.execute("UPDATE repo_areas_proposals SET job_id = 'job-map' WHERE id = 'areas-widget'")
        # A job whose config names the same map, which is not its own: no link.
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, pipeline_config, created_at, started_at, "
            "finished_at, dispatched_by) SELECT 'job-not-its', repo, task, status, pipeline_template, pipeline_config, "
            "created_at, started_at, finished_at, dispatched_by FROM jobs WHERE job_id = 'job-map'"
        )
        answer = await call(app, "GET", "/api/v1/decisions?stage=done", MASTER)
        assert answer.status_code == 200, answer.text
        cards = {d["id"]: d for d in answer.json()["decisions"]}
        assert cards["done:job-map"]["links"]["areas_map"] == "areas-widget"
        other = cards[f"done:{values[COVERED]['job_id']}"]
        assert "areas_map" not in other["links"], other["links"]
        assert "areas_map" not in cards["done:job-not-its"]["links"], "a card links a map that is not its job's"

        for status in ("applied", "rejected", "stale"):
            await db.execute("UPDATE repo_areas_proposals SET status = $1 WHERE id = 'areas-widget'", status)
            answer = await call(app, "GET", "/api/v1/decisions?stage=done", MASTER)
            card = {d["id"]: d for d in answer.json()["decisions"]}["done:job-map"]
            assert "areas_map" not in card["links"], f"the card still links a map that was {status}: {card['links']}"
