"""Rediscovery on a schedule (ADR 0014): only due owners start, never two runs at once."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import HTTPException

from embry0.services.org_rediscovery import OrgRediscoveryLoop, is_due
from embry0.services.organizations import Organization

T0 = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FakeOrgs:
    def __init__(self, *orgs: Organization) -> None:
        self.orgs = list(orgs)

    async def list_all(self, *, include_removed: bool = False) -> list[Organization]:
        return [o for o in self.orgs if include_removed or o.status != "removed"]


class FakeRuns:
    """org_discovery_runs in memory; ``list_runs`` answers newest first, as the repository does."""

    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    def add(self, owner: str, status: str, started: datetime, finished: datetime | None = None) -> dict[str, Any]:
        run = {
            "id": f"odr-{len(self.runs) + 1}",
            "owner": owner,
            "status": status,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat() if finished else None,
        }
        self.runs.append(run)
        return run

    async def running_run(self, owner: str) -> dict[str, Any] | None:
        return next((r for r in self.runs if r["owner"] == owner and r["status"] == "running"), None)

    async def list_runs(self, owner: str, *, limit: int = 20) -> list[dict[str, Any]]:
        mine = [r for r in self.runs if r["owner"] == owner]
        return sorted(mine, key=lambda r: r["started_at"], reverse=True)[:limit]


def org(owner: str, hours: Any = 6, *, status: str = "active") -> Organization:
    settings = {} if hours is None else {"rediscover_hours": hours}
    return Organization(owner=owner, forge="github", settings=settings, status=status)


def make_loop(orgs: FakeOrgs, runs: FakeRuns, clock: Clock, *, refuse: dict[str, Exception] | None = None) -> Any:
    """The loop over the fakes; its ``start_run`` opens a running run, as ``start_discovery`` does.

    ``attempts`` lists every call, refused or not; ``started`` the ones that opened a run.
    """
    started: list[str] = []
    attempts: list[str] = []

    async def start_run(organization: Organization) -> dict[str, Any]:
        attempts.append(organization.owner)
        failure = (refuse or {}).get(organization.owner)
        if failure is not None:
            raise failure
        started.append(organization.owner)
        return runs.add(organization.owner, "running", clock())

    loop = OrgRediscoveryLoop(organizations=orgs, runs=runs, start_run=start_run, clock=clock)
    loop.started = started  # type: ignore[attr-defined]
    loop.attempts = attempts  # type: ignore[attr-defined]
    return loop


def finish(runs: FakeRuns, owner: str, when: datetime, status: str = "done") -> None:
    run = next(r for r in runs.runs if r["owner"] == owner and r["status"] == "running")
    run.update(status=status, finished_at=when.isoformat())


@pytest.mark.asyncio
async def test_only_due_owners_start() -> None:
    runs = FakeRuns()
    runs.add("fresh", "done", T0 - timedelta(hours=2), T0 - timedelta(hours=1))
    runs.add("stale", "done", T0 - timedelta(hours=8), T0 - timedelta(hours=7))
    runs.add("off", "done", T0 - timedelta(days=30), T0 - timedelta(days=30))
    orgs = FakeOrgs(
        org("never"),  # on the schedule and never discovered
        org("fresh"),
        org("stale"),
        org("off", hours=None),
        org("paused", hours=1, status="paused"),
        org("removed", hours=1, status="removed"),
        # Values POST /orgs could have stored unvalidated: none is a schedule.
        org("text", hours="6"),
        org("flag", hours=True),
        org("zero", hours=0),
        org("long", hours=169),
    )
    loop = make_loop(orgs, runs, Clock(T0))
    assert await loop.tick() == ["never", "stale"]
    assert loop.started == ["never", "stale"]


@pytest.mark.asyncio
async def test_the_loop_never_double_starts_and_waits_a_full_interval() -> None:
    runs, clock = FakeRuns(), Clock(T0)
    loop = make_loop(FakeOrgs(org("acme", hours=6)), runs, clock)
    assert await loop.tick() == ["acme"]
    clock.now = T0 + timedelta(minutes=5)
    assert await loop.tick() == [], "a run in progress is never joined by a second"
    finish(runs, "acme", T0 + timedelta(minutes=20))
    clock.now = T0 + timedelta(hours=6, minutes=19)
    assert await loop.tick() == [], "due six hours after the run finished, not after it started"
    clock.now = T0 + timedelta(hours=6, minutes=20)
    assert await loop.tick() == ["acme"]
    assert loop.started == ["acme", "acme"]
    assert [r["status"] for r in runs.runs] == ["done", "running"]


@pytest.mark.asyncio
async def test_a_run_a_person_started_holds_the_schedule_off() -> None:
    runs = FakeRuns()
    runs.add("acme", "done", T0 - timedelta(days=2), T0 - timedelta(days=2))
    runs.add("acme", "running", T0 - timedelta(minutes=1))
    loop = make_loop(FakeOrgs(org("acme", hours=1)), runs, Clock(T0))
    assert await loop.tick() == []
    assert loop.started == []


@pytest.mark.asyncio
async def test_a_failed_or_interrupted_run_waits_an_interval_from_its_start() -> None:
    runs, clock = FakeRuns(), Clock(T0)
    # A dead credential: the run failed quickly. Retried once per interval, not once per tick.
    runs.add("dead", "error", T0, T0 + timedelta(minutes=1))
    # Cut off by a restart: the boot sweep closed it three hours after it began.
    runs.add("cut", "error", T0, T0 + timedelta(hours=3))
    loop = make_loop(FakeOrgs(org("dead"), org("cut")), runs, clock)
    clock.now = T0 + timedelta(hours=5, minutes=59)
    assert await loop.tick() == []
    clock.now = T0 + timedelta(hours=6)
    assert await loop.tick() == ["dead", "cut"]


@pytest.mark.asyncio
async def test_a_start_refused_before_any_run_exists_waits_a_full_interval() -> None:
    """The credential cannot be resolved, so the start is refused before a
    run row exists and nothing records the attempt: only the loop's memory
    of the refusal keeps the owner from being tried again on every tick."""
    runs, clock = FakeRuns(), Clock(T0)
    refuse: dict[str, Exception] = {"gone": HTTPException(status_code=422, detail="no github credential for gone")}
    loop = make_loop(FakeOrgs(org("gone", hours=6)), runs, clock, refuse=refuse)
    for minutes in (0, 5, 10, 15):
        clock.now = T0 + timedelta(minutes=minutes)
        assert await loop.tick() == []
    assert loop.attempts == ["gone"], "one attempt in fifteen minutes, not one per tick"
    assert runs.runs == [], "a refused start records no run"
    clock.now = T0 + timedelta(hours=5, minutes=59)
    await loop.tick()
    assert loop.attempts == ["gone"]
    clock.now = T0 + timedelta(hours=6)
    await loop.tick()
    assert loop.attempts == ["gone", "gone"], "tried again one interval after the refusal"
    del refuse["gone"]  # a person bound a working credential
    clock.now = T0 + timedelta(hours=11, minutes=59)
    assert await loop.tick() == [], "the second refusal is the anchor now"
    clock.now = T0 + timedelta(hours=12)
    assert await loop.tick() == ["gone"]


@pytest.mark.asyncio
async def test_one_owner_failing_to_start_does_not_stop_the_others() -> None:
    refuse: dict[str, Exception] = {
        "gone": HTTPException(status_code=422, detail="no github credential for gone"),
        "boom": RuntimeError("database went away"),
    }
    loop = make_loop(FakeOrgs(org("gone"), org("boom"), org("acme")), FakeRuns(), Clock(T0), refuse=refuse)
    assert await loop.tick() == ["acme"]


def test_is_due_reads_iso_strings_and_naive_times_as_utc() -> None:
    naive = (T0 - timedelta(hours=6)).replace(tzinfo=None)
    done = {"status": "done", "started_at": naive.isoformat(), "finished_at": naive.isoformat()}
    assert is_due(done, hours=6, now=T0)
    assert not is_due(done, hours=7, now=T0)
    assert is_due(None, hours=1, now=T0)
    assert not is_due({"status": "running", "started_at": T0.isoformat()}, hours=1, now=T0 + timedelta(days=9))


def test_is_due_reads_a_refusal_as_an_attempt() -> None:
    refused = T0 - timedelta(hours=1)
    assert not is_due(None, hours=6, now=T0, refused_at=refused), "a refusal anchors an owner with no run"
    assert is_due(None, hours=1, now=T0, refused_at=refused)
    done = {"status": "done", "started_at": (T0 - timedelta(days=2)).isoformat()}
    done["finished_at"] = done["started_at"]
    assert not is_due(done, hours=6, now=T0, refused_at=refused), "the newer of the run and the refusal"
    assert is_due(done, hours=6, now=T0, refused_at=None)
