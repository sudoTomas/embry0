"""Migration 121 — the slop-o-meter: each repository's history, forge events and weekly slop score."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import MIGRATIONS

TABLES = (
    "slop_meter_repos",
    "slop_meter_commits",
    "slop_meter_events",
    "slop_meter_logins",
    "slop_meter_judgments",
    "slop_meter_weeks",
    "slop_meter_scores",
)


def test_migration_121_present() -> None:
    versions = [v for v, _, _ in MIGRATIONS]
    assert versions.count(121) == 1, "migration 121 (slop-o-meter) must exist exactly once"
    sql = next(s for v, _, s in MIGRATIONS if v == 121)
    for table in TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql, table


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_meter_keeps_its_history_reading_and_scores(db_with_migrations: DatabasePool) -> None:
    from embry0.services.slop_meter.history import Commit
    from embry0.services.slop_meter.score import Meter, Week
    from embry0.storage.repositories.slop_meter import SlopMeterRepository

    store = SlopMeterRepository(db_with_migrations)
    at = datetime(2026, 1, 5, 9, tzinfo=UTC)
    assert (await store.state("acme/web"))["commits_read"] == 0
    commit = Commit("a1", at, "Ana", "ana@example.com", "Work", (("src/a.py", 10, 2),), (("Bo", "bo@example.com"),))
    await store.add_commits("acme/web", 0, [commit.to_row()])
    await store.add_commits("acme/web", 0, [commit.to_row()])  # stored once
    await store.set_state("acme/web", commits_read=1, commits_total=1, head_sha="a1")
    [row] = await store.commits("acme/web")
    assert Commit.from_row(row) == commit and await store.authors("acme/web") == ["ana@example.com"]
    await store.add_events("acme/web", [("ic:1", "comment", "ana", at), ("ic:1", "comment", "ana", at)])
    assert len(await store.events("acme/web")) == 1
    await store.set_login("Ana", "ana@example.com")
    assert await store.logins() == {"ana": "ana@example.com"}
    await store.judge("acme/web", "a1", slop=False, reason="moved", model="custom/m")
    assert await store.judgments("acme/web") == {"a1": False}

    week = Week(date(2026, 1, 5), 100.0, 60.0, 40.0, 8.0, 0.4)
    results = {
        "": Meter(0.4, 3, "Lasagna", False, [week], 1),
        "Api": Meter(0.1, 1, "Buttered Noodles", True, [week], 1),
    }
    await store.store("acme/web", results, at)
    await store.store("acme/web", results, at)  # replaced, not doubled
    scores = await store.scores("acme/web")
    assert [(s["area"], s["name"], s["provisional"]) for s in scores] == [
        ("", "Lasagna", False),
        ("Api", "Buttered Noodles", True),
    ]
    assert [w["slop"] for w in await store.weeks("acme/web", "Api")] == [40.0]

    await store.forget_history("acme/web")
    assert await store.commits("acme/web") == [] and (await store.state("acme/web"))["commits_read"] == 0
