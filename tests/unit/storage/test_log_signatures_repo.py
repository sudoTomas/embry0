"""log_signatures repository — the durable half of SH-P3.1 (#289).

These run against real Postgres because the behaviour under test IS the SQL:
LEAST/GREATEST on the seen-timestamps, the count accumulate-vs-replace split,
the services union, and the (signature_id, repo) composite key. A mocked
connection would assert that the string was sent, not that it does the right
thing — and the first version of this upsert was accepted by review and rejected
by Postgres ("inconsistent types deduced for parameter $6", one placeholder
bound to both a BIGINT and an INTEGER column).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.services.log_signatures import LogEntry, cluster_entries
from embry0.storage.repositories.log_signatures import (
    MAX_STORED_SAMPLES,
    LogSignaturesRepository,
)

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

_T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def _entries(line_prefix: str, offsets: list[int], service: str | None = None) -> list[LogEntry]:
    return [
        LogEntry(line=f"{line_prefix} {i}", timestamp=_T0 + timedelta(seconds=o), service=service)
        for i, o in enumerate(offsets)
    ]


async def test_first_window_records_counts_rate_services_and_samples(db_with_migrations) -> None:
    repo = LogSignaturesRepository(db_with_migrations)
    clusters = cluster_entries(_entries("ERROR boom", [0, 60], service="api"))

    assert await repo.record_window(clusters, repo="o/r") == 1

    row = await repo.get(clusters[0].signature_id, repo="o/r")
    assert row is not None
    assert row.occurrence_count == 2
    assert row.ticks_seen == 1
    assert row.last_window_count == 2
    assert row.span_seconds == 60
    assert row.rate_per_hour == 120.0
    assert row.services == ["api"]
    assert row.sample_lines == ["ERROR boom 0", "ERROR boom 1"]


async def test_seen_window_only_ever_widens(db_with_migrations) -> None:
    """first_seen moves only EARLIER and last_seen only LATER.

    An overlapping or replayed window must not be able to rewrite history and
    make a long-running signature look newly discovered — which is exactly the
    judgement #312 will hang a healed/not-healed verdict on.
    """
    repo = LogSignaturesRepository(db_with_migrations)
    first = cluster_entries(_entries("ERROR boom", [0, 60]))
    await repo.record_window(first, repo="o/r")

    # Second window straddles it: one line an hour earlier, one two hours later.
    await repo.record_window(cluster_entries(_entries("ERROR boom", [-3600, 7200])), repo="o/r")

    row = await repo.get(first[0].signature_id, repo="o/r")
    assert row is not None
    assert row.first_seen_at == _T0 - timedelta(hours=1)
    assert row.last_seen_at == _T0 + timedelta(hours=2)


async def test_counts_accumulate_but_last_window_count_replaces(db_with_migrations) -> None:
    """occurrence_count is a running total; last_window_count is "how loud was
    this in the most recent tick" and must not accumulate, or "getting worse?"
    becomes unanswerable."""
    repo = LogSignaturesRepository(db_with_migrations)
    clusters = cluster_entries(_entries("ERROR boom", [0, 10, 20]))
    await repo.record_window(clusters, repo="o/r")
    await repo.record_window(cluster_entries(_entries("ERROR boom", [30])), repo="o/r")

    row = await repo.get(clusters[0].signature_id, repo="o/r")
    assert row is not None
    assert row.occurrence_count == 4
    assert row.last_window_count == 1
    assert row.ticks_seen == 2


async def test_services_union_across_windows(db_with_migrations) -> None:
    repo = LogSignaturesRepository(db_with_migrations)
    clusters = cluster_entries(_entries("ERROR boom", [0], service="api"))
    await repo.record_window(clusters, repo="o/r")
    await repo.record_window(cluster_entries(_entries("ERROR boom", [10], service="ml")), repo="o/r")
    await repo.record_window(cluster_entries(_entries("ERROR boom", [20], service="api")), repo="o/r")

    row = await repo.get(clusters[0].signature_id, repo="o/r")
    assert row is not None
    assert sorted(row.services) == ["api", "ml"]


async def test_samples_are_kept_from_the_first_window_that_had_any(db_with_migrations) -> None:
    """The first concrete examples survive later churn — a signature's earliest
    samples are the ones a human has any chance of correlating with an incident."""
    repo = LogSignaturesRepository(db_with_migrations)
    clusters = cluster_entries(_entries("ERROR original", [0]))
    await repo.record_window(clusters, repo="o/r")
    # Same template (the digit is normalized away), different raw text.
    await repo.record_window(cluster_entries([LogEntry(line="ERROR original 99", timestamp=_T0)]), repo="o/r")

    row = await repo.get(clusters[0].signature_id, repo="o/r")
    assert row is not None
    assert row.sample_lines == ["ERROR original 0"]


async def test_samples_are_capped(db_with_migrations) -> None:
    repo = LogSignaturesRepository(db_with_migrations)
    clusters = cluster_entries(_entries("ERROR boom", list(range(20))), samples_per_cluster=10)
    await repo.record_window(clusters, repo="o/r")
    row = await repo.get(clusters[0].signature_id, repo="o/r")
    assert row is not None
    assert len(row.sample_lines) == MAX_STORED_SAMPLES


async def test_the_same_signature_in_two_repos_is_two_rows(db_with_migrations) -> None:
    """repo is in the primary key: two services emitting the same Spring
    exception are two faults, and pooling their rates would be meaningless."""
    repo = LogSignaturesRepository(db_with_migrations)
    clusters = cluster_entries(_entries("ERROR shared", [0, 5]))
    await repo.record_window(clusters, repo="a/one")
    await repo.record_window(clusters, repo="b/two")
    await repo.record_window(clusters, repo="b/two")

    a = await repo.get(clusters[0].signature_id, repo="a/one")
    b = await repo.get(clusters[0].signature_id, repo="b/two")
    assert a is not None and b is not None
    assert a.occurrence_count == 2
    assert b.occurrence_count == 4


async def test_unscoped_repo_is_stored_as_empty_string_not_null(db_with_migrations) -> None:
    """A NULL cannot sit in a primary key, so '' is the unscoped sentinel."""
    repo = LogSignaturesRepository(db_with_migrations)
    clusters = cluster_entries(_entries("ERROR unscoped", [0]))
    await repo.record_window(clusters, repo=None)
    row = await repo.get(clusters[0].signature_id)
    assert row is not None
    assert row.repo == ""


async def test_known_ids_answers_the_whole_window_in_one_query(db_with_migrations) -> None:
    repo = LogSignaturesRepository(db_with_migrations)
    clusters = cluster_entries(_entries("ERROR known", [0]))
    await repo.record_window(clusters, repo="o/r")

    known = await repo.known_ids([clusters[0].signature_id, "0000000000000000"], repo="o/r")
    assert known == {clusters[0].signature_id}
    # Scoped: the same id in another repo is not "known" here.
    assert await repo.known_ids([clusters[0].signature_id], repo="other/repo") == set()
    assert await repo.known_ids([], repo="o/r") == set()


async def test_top_ranks_by_occurrence_count(db_with_migrations) -> None:
    repo = LogSignaturesRepository(db_with_migrations)
    await repo.record_window(cluster_entries(_entries("ERROR loud", [0, 1, 2, 3])), repo="o/r")
    await repo.record_window(cluster_entries(_entries("ERROR quiet", [0])), repo="o/r")

    rows = await repo.top(repo="o/r", limit=10)
    assert [r.occurrence_count for r in rows] == [4, 1]
    assert "loud" in rows[0].template


async def test_a_cluster_without_timestamps_is_skipped_not_stamped_with_now(db_with_migrations) -> None:
    """Storing NOW() for an unknown first/last would invent a span, and every
    rate derived from it afterwards would be fiction."""
    repo = LogSignaturesRepository(db_with_migrations)
    clusters = cluster_entries(["ERROR no timestamp here"])
    assert await repo.record_window(clusters, repo="o/r") == 0
    assert await repo.get(clusters[0].signature_id, repo="o/r") is None


async def test_observed_at_supplies_the_timestamp_when_entries_lack_one(db_with_migrations) -> None:
    repo = LogSignaturesRepository(db_with_migrations)
    clusters = cluster_entries(["ERROR no timestamp here"])
    assert await repo.record_window(clusters, repo="o/r", observed_at=_T0) == 1
    row = await repo.get(clusters[0].signature_id, repo="o/r")
    assert row is not None
    assert row.first_seen_at == row.last_seen_at == _T0
    assert row.rate_per_hour is None


async def test_empty_window_writes_nothing(db_with_migrations) -> None:
    repo = LogSignaturesRepository(db_with_migrations)
    assert await repo.record_window([], repo="o/r") == 0


async def test_is_new_since_distinguishes_first_sighting_from_recurrence(db_with_migrations) -> None:
    repo = LogSignaturesRepository(db_with_migrations)
    clusters = cluster_entries(_entries("ERROR boom", [0]))
    await repo.record_window(clusters, repo="o/r")
    row = await repo.get(clusters[0].signature_id, repo="o/r")
    assert row is not None
    assert row.is_new_since(_T0 - timedelta(minutes=5)) is True
    assert row.is_new_since(_T0 + timedelta(minutes=5)) is False


async def test_samples_are_stored_as_a_json_array(db_with_migrations) -> None:
    """json.dumps before the pool's JSONB codec stored a JSON *string* (#382):
    the repository read it back through a tolerant decode, SQL saw a scalar."""
    repo = LogSignaturesRepository(db_with_migrations)
    clusters = cluster_entries(_entries("ERROR boom", [0, 60]))
    await repo.record_window(clusters, repo="o/r")
    kind = await db_with_migrations.fetchval(
        "SELECT jsonb_typeof(sample_lines) FROM log_signatures WHERE signature_id = $1", clusters[0].signature_id
    )
    assert kind == "array", f"sample_lines stored as {kind}"
    first = await db_with_migrations.fetchval(
        "SELECT sample_lines->>0 FROM log_signatures WHERE signature_id = $1", clusters[0].signature_id
    )
    assert first == "ERROR boom 0"


async def test_an_analysis_verdict_is_remembered_per_signature(db_with_migrations) -> None:
    """Spec 2026-10-25: the watcher's novelty gate reads back what the last analysis found, and when."""
    repo = LogSignaturesRepository(db_with_migrations)
    clean = cluster_entries(_entries("ERROR quiet", [0, 30, 60]))[0]
    noisy = cluster_entries(_entries("ERROR loud", [0]))[0]
    await repo.record_window([clean, noisy], repo="o/r")

    await repo.mark_analysed({clean.signature_id: 3, noisy.signature_id: 1}, repo="o/r", verdict="no_issue", at=_T0)
    await repo.mark_analysed({noisy.signature_id: 1}, repo="o/r", verdict="proposed", at=_T0 + timedelta(minutes=5))

    found = await repo.clean_since([clean.signature_id, noisy.signature_id], repo="o/r", since=_T0 - timedelta(hours=1))
    assert found == {clean.signature_id: 3}, "a signature whose last analysis proposed is not clean"
    assert await repo.clean_since([clean.signature_id], repo="o/r", since=_T0 + timedelta(minutes=1)) == {}
    assert await repo.clean_since([clean.signature_id], repo="other/r", since=_T0 - timedelta(hours=1)) == {}
