"""DigestRepository against a real database."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from embry0.orchestration import dispatch_actors
from embry0.storage.repositories.digest import DigestRepository

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

NOW = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)


async def _job(db, job_id, *, status, finished, dispatched_by=None, kind="code", repo="o/r", cost=1.0, pr=None):
    await db.execute(
        """
        INSERT INTO jobs (job_id, status, repo, task, job_kind, dispatched_by, total_cost_usd, pr_url,
                          created_at, finished_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $9)
        """,
        job_id,
        status,
        repo,
        f"Fix {job_id}\nbody",
        kind,
        dispatched_by,
        cost,
        pr,
        finished,
    )


async def test_claim_day_has_exactly_one_winner(db_with_migrations) -> None:
    repo = DigestRepository(db_with_migrations)
    assert await repo.claim_day(date(2026, 9, 21)) is True
    assert await repo.claim_day(date(2026, 9, 21)) is False
    await repo.release_day(date(2026, 9, 21))
    assert await repo.claim_day(date(2026, 9, 21)) is True, "a released day could not be retried"


async def test_a_sent_day_cannot_be_released(db_with_migrations) -> None:
    repo = DigestRepository(db_with_migrations)
    await repo.claim_day(date(2026, 9, 21))
    await repo.mark_sent(date(2026, 9, 21), {"x": 1})
    await repo.release_day(date(2026, 9, 21))
    assert await repo.claim_day(date(2026, 9, 21)) is False, "releasing re-opened a day that was already sent"


async def test_collect_separates_the_watcher_from_real_work(db_with_migrations) -> None:
    db = db_with_migrations
    inside = NOW - timedelta(hours=3)
    await _job(db, "j-done", status="completed", finished=inside, pr="https://github.com/o/r/pull/1", cost=2.5)
    await _job(db, "j-fail", status="failed", finished=inside, cost=0.5)
    await _job(
        db,
        "j-watch",
        status="completed",
        finished=inside,
        dispatched_by=dispatch_actors.WATCHER,
        kind="analysis",
        repo=None,
        cost=0.4,
    )
    await _job(db, "j-old-watch", status="completed", finished=inside, kind="analysis", repo=None, cost=0.3)
    await _job(db, "j-yesterday", status="completed", finished=NOW - timedelta(days=2))

    out = await DigestRepository(db).collect(
        since=NOW - timedelta(days=1), until=NOW, watcher_actor=dispatch_actors.WATCHER
    )
    assert out["jobs"] == {"completed": 1, "failed": 1}, "watcher jobs or out-of-window jobs were counted as work"
    assert out["job_spend_usd"] == 3.0
    assert out["watcher_spend_usd"] == 0.7, "pre-dispatched_by analysis jobs were not counted as the watcher's"
    titles = {j["title"] for j in out["jobs_listed"]}
    assert titles == {"Fix j-done", "Fix j-fail"}
    assert next(j for j in out["jobs_listed"] if j["job_id"] == "j-done")["pr_url"].endswith("/pull/1")


async def test_a_merged_pr_job_is_listed(db_with_migrations) -> None:
    """`pr_merged` is a job status of its own; it is the best news of the day."""
    db = db_with_migrations
    await _job(
        db, "j-merged", status="pr_merged", finished=NOW - timedelta(hours=1), pr="https://github.com/o/r/pull/9"
    )
    out = await DigestRepository(db).collect(
        since=NOW - timedelta(days=1), until=NOW, watcher_actor=dispatch_actors.WATCHER
    )
    assert out["jobs"] == {"pr_merged": 1}
    assert [j["job_id"] for j in out["jobs_listed"]] == ["j-merged"], "the merged PR was left out of the list"


async def test_the_sent_payload_is_stored_as_a_json_object(db_with_migrations) -> None:
    """json.dumps before the pool's JSONB codec stored a JSON *string* (#382),
    so the payload kept "to check a digest against" was unreadable in SQL."""
    repo = DigestRepository(db_with_migrations)
    await repo.claim_day(date(2026, 9, 21))
    await repo.mark_sent(date(2026, 9, 21), {"jobs": {"completed": 2}, "since": NOW})
    row = await db_with_migrations.fetchrow(
        "SELECT jsonb_typeof(payload) AS kind, payload->'jobs'->>'completed' AS completed, payload->>'since' AS since "
        "FROM digest_deliveries WHERE day = $1",
        date(2026, 9, 21),
    )
    assert row["kind"] == "object", f"payload stored as {row['kind']}"
    assert row["completed"] == "2"
    assert row["since"].startswith("2026-09-21"), "a datetime in the payload must still serialize"


async def test_collect_reports_what_background_calls_spent_in_the_window(db_with_migrations) -> None:
    """The briefs and the Slop-O-Meter's judge spend outside any job, in ``background_spend``: the day's
    digest reports what they spent that day, by feature, and nothing of the day before or after (#164)."""
    from embry0.storage.repositories import digest

    db = db_with_migrations
    since, until = NOW - timedelta(days=1), NOW
    for feature, cost, at in [
        ("job_brief", 0.25, since),  # the window's first instant is in it
        ("job_brief", 0.5, NOW - timedelta(hours=3)),
        ("slop_judge", 1.0, NOW - timedelta(hours=2)),
        ("proposal_brief", 9.0, since - timedelta(seconds=1)),  # the day before
        ("proposal_brief", 9.0, until),  # the next day's first instant
    ]:
        await db.execute(
            "INSERT INTO background_spend (feature, model, cost_usd, at) VALUES ($1, 'custom/m', $2, $3)",
            feature,
            cost,
            at,
        )
    await _job(db, "j-done", status="completed", finished=NOW - timedelta(hours=3), cost=2.5)

    out = await DigestRepository(db).collect(since=since, until=until, watcher_actor=dispatch_actors.WATCHER)
    assert out.get("background_spend_by_feature") == {"job_brief": 0.75, "slop_judge": 1.0}, (
        f"the digest leaves out what background calls spent that day, or counts another day's: {out}"
    )
    assert out["background_spend_usd"] == 1.75
    assert out["job_spend_usd"] == 2.5, "a background call was counted as a job's spend"

    sql = getattr(digest, "_BACKGROUND_SPEND_SQL", "")
    assert sql, "the digest reads background_spend"
    async with db.transaction() as conn:
        await conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(r[0] for r in await conn.fetch("EXPLAIN " + sql, since, until))
    ranges = [line for line in plan.splitlines() if "Index Cond" in line and "at >=" in line and "at <" in line]
    assert "idx_background_spend_at" in plan and ranges, (
        f"the day's read is a range of the index, not a pass over the whole table: {plan}"
    )


async def test_collect_reports_no_background_spend_on_a_quiet_day(db_with_migrations) -> None:
    out = await DigestRepository(db_with_migrations).collect(
        since=NOW - timedelta(days=1), until=NOW, watcher_actor=dispatch_actors.WATCHER
    )
    assert out.get("background_spend_usd") == 0 and out.get("background_spend_by_feature") == {}, (
        f"a day with no background call reports none: {out}"
    )
