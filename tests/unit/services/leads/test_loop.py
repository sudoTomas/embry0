"""The leads loop (spec 2026-10-25): what it stores, caps and closes, and what it never closes.

Its policy is the ``leads`` section of the settings document, read through
``tests.fixtures.leads`` as the real snapshot reads it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from embry0.execution.forge_client import Listing
from embry0.services.leads import Lead
from embry0.services.leads.loop import LeadsLoop
from tests.fixtures.leads import MemorySettings
from tests.unit.services.leads.fakes import FakeForge, MemoryLeads
from tests.unit.services.leads.test_backlog import READY_BODY

REPO = "acme/web"
HEAD = "c" * 40
T0 = datetime(2026, 10, 25, 12, tzinfo=UTC)


def _ci(*failing: str) -> FakeForge:
    return FakeForge(
        {
            f"/repos/{REPO}": {"default_branch": "main"},
            f"/repos/{REPO}/commits/main": {"sha": HEAD},
            f"/repos/{REPO}/commits/{HEAD}/check-runs": {
                "total_count": len(failing),
                "check_runs": [{"name": n, "status": "completed", "conclusion": "failure"} for n in failing],
            },
            f"/repos/{REPO}/commits/{HEAD}/status": {"statuses": []},
        }
    )


def _loop(store: MemoryLeads, client: Any, *, finders: set[str], linear: Any = None, cap: int = 5, now=T0) -> LeadsLoop:
    clock = {"now": now}
    settings = MemorySettings({"enabled": True, "finders": sorted(finders), "max_new_per_repo_per_day": cap})

    async def targets(section: Any = None) -> list[str]:
        return [REPO]

    async def client_for(repo: str) -> Any:
        return client

    async def get_linear() -> Any:
        return linear

    async def tracked(tracker: str, repo: str, key: str) -> bool:
        return False

    async def triggers() -> list[str]:
        return ["embry0"]

    async def area_of(repo: str, client: Any, lead: Lead) -> str | None:
        return "Web" if lead.kind == "ci" else None

    loop = LeadsLoop(
        settings=settings.leads,
        leads=store,
        targets=targets,
        client_for=client_for,
        linear=get_linear,
        tracked=tracked,
        trigger_labels=triggers,
        area_of=area_of,
        clock=lambda: clock["now"],
    )
    return loop


async def test_what_a_finder_sees_is_stored_with_its_area_and_closed_once_it_is_gone():
    store = MemoryLeads()
    summary = await _loop(store, _ci("build"), finders={"ci"}).tick()
    assert summary == {REPO: {"ci": {"found": 1, "new": 1}}}
    (row,) = store.open("ci")
    assert row["area"] == "Web" and row["source_key"] == "check:build"
    summary = await _loop(store, _ci(), finders={"ci"}, now=T0 + timedelta(hours=6)).tick()
    assert summary[REPO]["ci"] == {"gone": 1} and not store.open("ci")
    await _loop(store, _ci("build"), finders={"ci"}, now=T0 + timedelta(hours=12)).tick()
    (back,) = store.open("ci")
    assert back["first_seen"] == T0, "a lead seen again keeps when it was first seen"


async def test_each_finder_adds_at_most_its_daily_cap_of_new_leads():
    store = MemoryLeads()
    checks = [f"check-{i}" for i in range(7)]
    summary = await _loop(store, _ci(*checks), finders={"ci"}, cap=5).tick()
    assert summary[REPO]["ci"] == {"found": 7, "new": 5, "capped": 2}
    assert len(store.open("ci")) == 5
    again = await _loop(store, _ci(*checks), finders={"ci"}, cap=5, now=T0 + timedelta(hours=6)).tick()
    assert again[REPO]["ci"] == {"found": 7, "capped": 2}, "the day's cap is spent; the stored ones stay"
    tomorrow = await _loop(store, _ci(*checks), finders={"ci"}, cap=5, now=T0 + timedelta(days=1, hours=1)).tick()
    assert tomorrow[REPO]["ci"] == {"found": 7, "new": 2}


async def test_a_finder_that_cannot_read_closes_nothing_and_says_so_once():
    store = MemoryLeads()
    await _loop(store, _ci("build"), finders={"ci"}).tick()
    summary = await _loop(store, FakeForge({}), finders={"ci"}, now=T0 + timedelta(hours=6)).tick()
    assert summary[REPO]["ci"] == {"skipped": "the default branch could not be read"}
    assert len(store.open("ci")) == 1
    nothing = await _loop(store, None, finders={"ci", "alert"}).tick(backlog=False)
    assert nothing[REPO] == {"ci": {"skipped": "no forge client"}, "alert": {"skipped": "no forge client"}}


def _linear(*keys: str, fails: bool = False) -> Any:
    async def list_backlog_tickets() -> tuple[list[Any], bool]:
        if fails:
            raise RuntimeError("Linear is down")
        tickets = [
            SimpleNamespace(
                issue=SimpleNamespace(
                    key=k, title=f"Fix the export for {k}", body=READY_BODY, url=f"u/{k}", labels=["bug"]
                ),
                repo=REPO,
                assigned=False,
                mine=False,
                priority=0,
                estimate=None,
            )
            for k in keys
        ]
        return tickets, True

    return SimpleNamespace(list_backlog_tickets=list_backlog_tickets)


async def test_the_backlog_reads_both_trackers_and_keeps_leads_from_one_it_could_not_read():
    issues = [{"number": 9, "title": "Export fails on an empty start date", "body": READY_BODY, "labels": []}]
    client = FakeForge({f"/repos/{REPO}/issues": Listing(issues, complete=True)})
    store = MemoryLeads()
    summary = await _loop(store, client, finders={"ticket"}, linear=_linear("ENG-1", "ENG-2")).tick()
    assert summary[REPO]["ticket"] == {"found": 3, "new": 3}
    assert sorted(r["source_key"] for r in store.open("ticket")) == ["github:9", "linear:ENG-1", "linear:ENG-2"]
    later = T0 + timedelta(days=1, hours=1)
    await _loop(store, client, finders={"ticket"}, linear=_linear(fails=True), now=later).tick()
    assert len(store.open("ticket")) == 3, "Linear unread: its leads stay open"
    await _loop(store, client, finders={"ticket"}, linear=_linear("ENG-1"), now=later + timedelta(days=1)).tick()
    assert sorted(r["source_key"] for r in store.open("ticket")) == ["github:9", "linear:ENG-1"]


async def test_the_backlog_waits_for_its_own_interval():
    store = MemoryLeads()
    loop = _loop(store, FakeForge({}), finders={"ticket"}, linear=_linear("ENG-1"))
    assert "ticket" in (await loop.tick())[REPO]
    assert "ticket" not in (await loop.tick())[REPO], "the second pass of the day leaves the backlog alone"
