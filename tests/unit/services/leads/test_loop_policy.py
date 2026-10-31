"""The leads loop reads its policy from the settings document's ``leads`` section (#168).

- The schedule always runs: it does nothing while ``leads.enabled`` is false,
  runs a pass as soon as it is turned on, and follows ``interval_hours`` as
  the section says it at each look.
- Each repository's pass follows its organization's layer: its finders, its
  daily cap and the readiness a backlog ticket needs to be offered
  (``offer_min``); the backlog is read only when some repository runs it.
- A pass by hand (``POST /leads/run``) runs whether leads are on or not.

The section is read through ``tests.fixtures.leads``, the real snapshot, so
each test fails with its own message on a tree that has no leads section.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from embry0.services.leads import FinderResult, Lead
from embry0.services.leads.loop import LeadsLoop
from tests.fixtures.leads import MemorySettings, leads_section
from tests.unit.services.leads.fakes import FakeForge, MemoryLeads
from tests.unit.services.leads.test_backlog import READY_BODY

T0 = datetime(2026, 10, 30, 12, tzinfo=UTC)
HEAD = "d" * 40
ACME = "acme/web"
GLOBEX = "globex/api"


def _failing(*repos_checks: tuple[str, list[str]]) -> FakeForge:
    """A forge where each repository's default branch has these checks failing; nothing else answers."""
    answers: dict[str, Any] = {}
    for repo, failing in repos_checks:
        answers |= {
            f"/repos/{repo}": {"default_branch": "main"},
            f"/repos/{repo}/commits/main": {"sha": HEAD},
            f"/repos/{repo}/commits/{HEAD}/check-runs": {
                "total_count": len(failing),
                "check_runs": [{"name": n, "status": "completed", "conclusion": "failure"} for n in failing],
            },
            f"/repos/{repo}/commits/{HEAD}/status": {"statuses": []},
        }
    return FakeForge(answers)


class Linear:
    """A Linear backlog with one ready ticket per repository; counts its reads."""

    def __init__(self, *repos: str) -> None:
        self.repos = repos
        self.reads = 0

    async def list_backlog_tickets(self) -> tuple[list[Any], bool]:
        self.reads += 1
        tickets = [
            SimpleNamespace(
                issue=SimpleNamespace(
                    key=f"ENG-{i}", title=f"Fix the export for ENG-{i}", body=READY_BODY, url="u", labels=["bug"]
                ),
                repo=repo,
                assigned=False,
                mine=False,
                priority=0,
                estimate=None,
            )
            for i, repo in enumerate(self.repos, start=1)
        ]
        return tickets, True


def _loop(
    settings: MemorySettings,
    store: MemoryLeads,
    client: Any,
    clock: list[datetime],
    *,
    repos: tuple[str, ...] = (ACME,),
    linear: Any = None,
) -> LeadsLoop:
    leads_section()

    async def targets(section: Any = None) -> list[str]:
        return list(repos)

    async def client_for(repo: str) -> Any:
        return client

    async def get_linear() -> Any:
        return linear

    async def tracked(tracker: str, repo: str, key: str) -> bool:
        return False

    async def triggers() -> list[str]:
        return ["embry0"]

    async def no_area(repo: str, client: Any, lead: Lead) -> str | None:
        return None

    return LeadsLoop(
        settings=settings.leads,
        leads=store,
        targets=targets,
        client_for=client_for,
        linear=get_linear,
        tracked=tracked,
        trigger_labels=triggers,
        area_of=no_area,
        clock=lambda: clock[0],
    )


async def test_the_schedule_idles_while_leads_are_off_and_runs_a_pass_once_they_are_on() -> None:
    settings = MemorySettings({"enabled": False, "finders": ["ci"]})
    forge = _failing((ACME, ["build"]))
    store = MemoryLeads()
    clock = [T0]
    loop = _loop(settings, store, forge, clock)

    assert await loop.scheduled() is None, "leads off: the look runs no pass"
    clock[0] += timedelta(days=2)
    assert await loop.scheduled() is None, "still off two days later: nothing, however overdue"
    assert (forge.asked, store.rows) == ([], {}), "while off the loop reads no forge and stores nothing"

    settings.deployment["enabled"] = True
    first = await loop.scheduled()
    assert first == {ACME: {"ci": {"found": 1, "new": 1}}}, f"turned on: the next look runs a pass: {first}"
    assert await loop.scheduled() is None, "the next look, at once, waits out interval_hours"

    clock[0] += timedelta(hours=6)
    assert await loop.scheduled() is not None, "six hours on, the default interval, the next pass runs"

    settings.deployment["enabled"] = False
    clock[0] += timedelta(hours=12)
    assert await loop.scheduled() is None, "turned off again: no pass, however overdue"


async def test_a_new_interval_reaches_the_next_look() -> None:
    settings = MemorySettings({"enabled": True, "finders": ["ci"], "interval_hours": 12})
    clock = [T0]
    loop = _loop(settings, MemoryLeads(), _failing((ACME, ["build"])), clock)
    assert await loop.scheduled() is not None, "the first look runs the first pass"

    clock[0] += timedelta(hours=2)
    assert await loop.scheduled() is None, "two hours of a twelve-hour interval: no pass"
    settings.deployment["interval_hours"] = 1
    assert await loop.scheduled() is not None, "the interval is now one hour, and two have gone by: a pass runs"


async def test_a_pass_by_hand_runs_whether_leads_are_on_or_off() -> None:
    settings = MemorySettings({"enabled": False, "finders": ["ci"]})
    loop = _loop(settings, MemoryLeads(), _failing((ACME, ["build"])), [T0])
    summary = await loop.tick()
    assert summary == {ACME: {"ci": {"found": 1, "new": 1}}}, (
        f"POST /leads/run runs one pass by hand while leads are off: {summary}"
    )


async def test_each_repository_s_pass_follows_its_organization_s_finders_and_cap() -> None:
    settings = MemorySettings(
        {"enabled": True, "finders": ["ci", "alert"], "max_new_per_repo_per_day": 5},
        {"acme": {"finders": ["ci"], "max_new_per_repo_per_day": 1}},
    )
    forge = _failing((ACME, ["build", "lint", "test"]), (GLOBEX, ["build", "lint", "test"]))
    summary = await _loop(settings, MemoryLeads(), forge, [T0], repos=(ACME, GLOBEX)).tick()

    assert set(summary[ACME]) == {"ci"}, f"acme's layer runs ci only: {summary[ACME]}"
    assert summary[ACME]["ci"] == {"found": 3, "new": 1, "capped": 2}, "and adds one new lead a day, its own cap"
    assert set(summary[GLOBEX]) == {"ci", "alert"}, f"globex has no layer: the deployment's finders: {summary[GLOBEX]}"
    assert summary[GLOBEX]["ci"] == {"found": 3, "new": 3}, "and the deployment's cap of five"


async def test_a_backlog_ticket_is_offered_by_its_organization_s_offer_min() -> None:
    """The ready ticket scores 75: below acme's offer_min of 80, above the deployment's 40."""
    settings = MemorySettings({"enabled": True, "finders": ["ticket"]}, {"acme": {"offer_min": 80}})
    linear = Linear(ACME, GLOBEX)
    store = MemoryLeads()
    summary = await _loop(settings, store, None, [T0], repos=(ACME, GLOBEX), linear=linear).tick()

    assert summary[ACME]["ticket"] == {}, f"acme's offer_min of 80 offers no ticket scoring 75: {summary[ACME]}"
    assert summary[GLOBEX]["ticket"] == {"found": 1, "new": 1}, f"the deployment's 40 offers it: {summary[GLOBEX]}"
    assert [row["repo"] for row in store.rows.values()] == [GLOBEX]


async def test_the_backlog_is_read_only_when_some_repository_runs_the_ticket_finder() -> None:
    settings = MemorySettings({"enabled": True, "finders": ["ci"]}, {"acme": {"finders": ["ticket"]}})
    linear = Linear(ACME, GLOBEX)
    forge = _failing((ACME, []), (GLOBEX, []))
    summary = await _loop(settings, MemoryLeads(), forge, [T0], repos=(ACME, GLOBEX), linear=linear).tick()
    assert linear.reads == 1, "acme's layer runs the ticket finder, so the backlog is read once"
    assert "ticket" in summary[ACME] and "ticket" not in summary[GLOBEX], (
        f"only acme's repositories take backlog tickets: {summary}"
    )

    nobody = MemorySettings({"enabled": True, "finders": ["ci"]})
    other = Linear(ACME)
    await _loop(nobody, MemoryLeads(), forge, [T0], linear=other).tick(backlog=True)
    assert other.reads == 0, "no repository runs the ticket finder: forcing the backlog reads nothing"


async def test_the_flaky_and_review_finders_read_each_organization_s_depth_and_staleness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """acme's layer reads 20 commits and counts a review stale after 7 days; globex reads the deployment's 10 and 3."""
    settings = MemorySettings(
        {"enabled": True, "finders": ["flaky", "review"], "flaky_commits": 10, "review_stale_days": 3},
        {"acme": {"flaky_commits": 20, "review_stale_days": 7}},
    )
    seen: dict[str, dict[str, Any]] = {}

    async def flaky(client: Any, repo: str, *, commits: int) -> FinderResult:
        seen.setdefault(repo, {})["commits"] = commits
        return FinderResult([])

    async def reviews(client: Any, repo: str, *, now: datetime, stale_days: int) -> FinderResult:
        seen.setdefault(repo, {})["stale_days"] = stale_days
        return FinderResult([])

    monkeypatch.setattr("embry0.services.leads.loop.find_flaky_checks", flaky)
    monkeypatch.setattr("embry0.services.leads.loop.find_unanswered_reviews", reviews)
    await _loop(settings, MemoryLeads(), FakeForge({}), [T0], repos=(ACME, GLOBEX)).tick()
    assert seen == {
        ACME: {"commits": 20, "stale_days": 7},
        GLOBEX: {"commits": 10, "stale_days": 3},
    }, f"each repository's finders read its organization's flaky_commits and review_stale_days: {seen}"


async def test_passes_come_interval_hours_after_the_last_one_ended_as_before_the_section() -> None:
    """Before the section the loop slept the interval after each pass. With an 18-minute interval and
    7-minute passes, after the 5-minute start delay, passes start at 5, 30 and 55 minutes, not at the next
    5-minute look after each is due."""
    settings = MemorySettings({"enabled": True, "finders": ["ci"], "interval_hours": 0.3})
    clock = [T0]
    starts: list[float] = []

    class Stop(Exception):
        pass

    async def sleep(seconds: float) -> None:
        clock[0] += timedelta(seconds=seconds)
        if clock[0] - T0 > timedelta(minutes=70):
            raise Stop

    loop = _loop(settings, MemoryLeads(), None, clock)
    assert "sleep" in LeadsLoop.__dataclass_fields__, "the schedule's sleep is given, so its timing can be read"

    async def targets(section: Any = None) -> list[str]:
        starts.append((clock[0] - T0).total_seconds() / 60)
        clock[0] += timedelta(minutes=7)
        return [ACME]

    loop.targets = targets
    loop.sleep = sleep
    with pytest.raises(Stop):
        await loop.start()
    assert starts == [5.0, 30.0, 55.0], f"each pass starts 18 minutes after the last one ended: {starts}"
