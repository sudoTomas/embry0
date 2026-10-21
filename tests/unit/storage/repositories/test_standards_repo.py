"""StandardsRepository and RepoStandardStateRepository against Postgres (ADR 0014, migration 102)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.standards import (
    RepoStandardStateRepository,
    StandardsRepository,
    new_standard_id,
)

pytestmark = [pytest.mark.requires_postgres]

SCHEMA = {"roles": {"color.accent": {"type": "color"}}, "rules": [{"id": "one-accent", "text": "One accent."}]}
FINISHED = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def test_new_standard_id_shape() -> None:
    sid = new_standard_id()
    assert sid.startswith("std-") and len(sid) == 16


async def test_a_put_supersedes_the_active_version(db_with_migrations: DatabasePool) -> None:
    repo = StandardsRepository(db_with_migrations)
    assert await repo.get_active("acme", "design") is None
    first, superseded = await repo.put(
        "Acme",
        "design",
        schema=SCHEMA,
        values={"color.accent": "#3366ff"},
        prose="One accent.",
        references=["acme/site"],
        created_by="admin@example.com",
    )
    assert superseded is None
    assert (first.owner, first.kind, first.version, first.status) == ("acme", "design", 1, "active")
    assert (first.values, first.prose, first.references, first.created_by) == (
        {"color.accent": "#3366ff"},
        "One accent.",
        ["acme/site"],
        "admin@example.com",
    )
    second, superseded = await repo.put("acme", "design", schema=SCHEMA, values={"color.accent": "#cc3333"})
    assert (second.version, second.status, superseded) == (2, "active", 1)
    assert (second.prose, second.references) == ("", []), "a version carries only what it was given"
    active = await repo.get_active("ACME", "design")
    assert active is not None and active.id == second.id
    versions = await repo.list_versions("acme", "design")
    assert [(v.version, v.status) for v in versions] == [(2, "active"), (1, "superseded")]
    assert versions[1].values == {"color.accent": "#3366ff"}, "a superseded version keeps its values"
    assert [s.id for s in await repo.list_active("acme")] == [second.id]
    assert await repo.list_active("other") == []


async def test_concurrent_puts_get_consecutive_versions(db_with_migrations: DatabasePool) -> None:
    repo = StandardsRepository(db_with_migrations)
    results = await asyncio.gather(*(repo.put("acme", "design", schema={}, values={}, prose=str(i)) for i in range(5)))
    assert sorted(standard.version for standard, _ in results) == [1, 2, 3, 4, 5]
    versions = await repo.list_versions("acme", "design")
    assert [v.status for v in versions] == ["active"] + ["superseded"] * 4


async def test_an_unknown_kind_or_owner_is_refused_before_sql(db_with_migrations: DatabasePool) -> None:
    repo = StandardsRepository(db_with_migrations)
    with pytest.raises(ValueError, match="unknown standard kind"):
        await repo.put("acme", "layout", schema={}, values={})
    with pytest.raises(ValueError, match="no slash"):
        await repo.get_active("acme/widgets", "design")


async def test_a_mode_is_set_lowercase_and_exceptions_are_kept_unless_replaced(
    db_with_migrations: DatabasePool,
) -> None:
    states = RepoStandardStateRepository(db_with_migrations)
    assert await states.get("acme/widgets", "design") is None
    exceptions = [{"rule": "one-accent", "reason": "charts", "paths": ["src/charts"]}]
    state = await states.set_mode("Acme/Widgets", "design", "advise", exceptions=exceptions)
    assert (state.repo, state.owner, state.kind, state.mode) == ("acme/widgets", "acme", "design", "advise")
    assert state.exceptions == exceptions
    kept = await states.set_mode("acme/widgets", "design", "enforce")
    assert (kept.mode, kept.exceptions) == ("enforce", exceptions), "omitted exceptions are kept"
    cleared = await states.set_mode("acme/widgets", "design", "off", exceptions=[])
    assert (cleared.mode, cleared.exceptions) == ("off", [])
    fetched = await states.get("ACME/widgets", "design")
    assert fetched is not None and fetched.mode == "off"
    await states.set_mode("acme/gadgets", "design", "advise")
    await states.set_mode("other/thing", "design", "advise")
    assert [s.repo for s in await states.list_for_owner("acme")] == ["acme/gadgets", "acme/widgets"]
    assert [s.repo for s in await states.list_for_owner("acme", "design")] == ["acme/gadgets", "acme/widgets"]
    assert [s.kind for s in await states.list_for_repo("Acme/Gadgets")] == ["design"]
    with pytest.raises(ValueError, match="unknown mode"):
        await states.set_mode("acme/widgets", "design", "strict")


async def test_an_audit_starts_only_outside_mode_off(db_with_migrations: DatabasePool) -> None:
    jobs = JobsRepository(db_with_migrations)
    states = RepoStandardStateRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="audit")
    assert await states.start_audit("acme/widgets", "design", job_id, expected_job_id=None) is None, (
        "no row is mode off"
    )
    await states.set_mode("acme/widgets", "design", "off")
    assert await states.start_audit("acme/widgets", "design", job_id, expected_job_id=None) is None
    await states.set_mode("acme/widgets", "design", "advise")
    started = await states.start_audit("Acme/Widgets", "design", job_id, expected_job_id=None)
    assert started is not None and started.last_audit_job_id == job_id


async def test_an_audit_starts_only_over_the_audit_its_caller_read(db_with_migrations: DatabasePool) -> None:
    """start_audit is a compare-and-set: of callers that read the same newest audit, exactly one wins."""
    jobs = JobsRepository(db_with_migrations)
    states = RepoStandardStateRepository(db_with_migrations)
    await states.set_mode("acme/widgets", "design", "advise")
    first = await jobs.create(repo="acme/widgets", task="audit 1")
    assert await states.start_audit("acme/widgets", "design", first, expected_job_id=None) is not None
    second = await jobs.create(repo="acme/widgets", task="audit 2")
    stale = await states.start_audit("acme/widgets", "design", second, expected_job_id=None)
    assert stale is None, "a caller that read no audit loses to the one that started first"
    contenders = [await jobs.create(repo="acme/widgets", task=f"audit {i}") for i in range(3, 9)]
    results = await asyncio.gather(
        *(states.start_audit("acme/widgets", "design", j, expected_job_id=first) for j in contenders)
    )
    winners = [r.last_audit_job_id for r in results if r is not None]
    assert len(winners) == 1 and winners[0] in contenders
    state = await states.get("acme/widgets", "design")
    assert state is not None and state.last_audit_job_id == winners[0]


async def test_only_the_newest_audit_records_its_result(db_with_migrations: DatabasePool) -> None:
    jobs = JobsRepository(db_with_migrations)
    states = RepoStandardStateRepository(db_with_migrations)
    older = await jobs.create(repo="acme/widgets", task="audit 1")
    newer = await jobs.create(repo="acme/widgets", task="audit 2")
    await states.set_mode("acme/widgets", "design", "advise")
    await states.start_audit("acme/widgets", "design", older, expected_job_id=None)
    await states.start_audit("acme/widgets", "design", newer, expected_job_id=older)
    assert await states.record_audit(older, score=10, findings_open=9, finished_at=FINISHED) is None
    assert await states.record_audit("job-unrelated", score=10, findings_open=9, finished_at=FINISHED) is None
    recorded = await states.record_audit(newer, score=72, findings_open=3, finished_at=FINISHED)
    assert recorded is not None
    assert (recorded.score, recorded.findings_open, recorded.last_audit_at, recorded.last_audit_job_id) == (
        72,
        3,
        FINISHED,
        newer,
    )
    unscored = await states.record_audit(newer, score=None, findings_open=0, finished_at=FINISHED)
    assert unscored is not None and unscored.score is None


async def test_a_purged_audit_job_keeps_the_score(db_with_migrations: DatabasePool) -> None:
    jobs = JobsRepository(db_with_migrations)
    states = RepoStandardStateRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="audit")
    await states.set_mode("acme/widgets", "design", "advise")
    await states.start_audit("acme/widgets", "design", job_id, expected_job_id=None)
    await states.record_audit(job_id, score=80, findings_open=1, finished_at=FINISHED)
    await db_with_migrations.execute("DELETE FROM jobs WHERE job_id = $1", job_id)
    state = await states.get("acme/widgets", "design")
    assert state is not None
    assert (state.last_audit_job_id, state.score, state.findings_open) == (None, 80, 1)


async def test_an_audit_that_finishes_in_mode_off_records_nothing(db_with_migrations: DatabasePool) -> None:
    """An admin switched the repository off while its audit ran: the run's result is dropped, not stored."""
    jobs = JobsRepository(db_with_migrations)
    states = RepoStandardStateRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/widgets", task="audit")
    await states.set_mode("acme/widgets", "design", "advise")
    await states.start_audit("acme/widgets", "design", job_id, expected_job_id=None)
    await states.set_mode("acme/widgets", "design", "off")
    assert await states.record_audit(job_id, score=72, findings_open=3, finished_at=FINISHED) is None
    state = await states.get("acme/widgets", "design")
    assert state is not None
    assert (state.mode, state.score, state.findings_open, state.last_audit_at) == ("off", None, 0, None)
