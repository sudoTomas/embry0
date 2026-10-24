"""Progression (spec 2026-10-24): the classic curve, what each event is worth, and the levels it adds up to."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.services import progression
from embry0.services.progression import (
    INSTANCE,
    MAX_LEVEL,
    STATS,
    ProgressionService,
    compute,
    level_for_xp,
    price,
    stat_of_agent,
    tier_for_level,
    xp_for_level,
)
from embry0.storage.repositories.progression import ProgressEvent

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def ev(source: str, agent: str | None = None, minutes: int = 0, report: str | None = None) -> ProgressEvent:
    return ProgressEvent(source, agent, T0 + timedelta(minutes=minutes), report)


# ---- the curve ----


@pytest.mark.parametrize(
    ("level", "xp"),
    [(1, 0), (2, 83), (10, 1_154), (50, 101_333), (92, 6_517_253), (99, 13_034_431)],
)
def test_the_curve_matches_its_published_points(level: int, xp: int) -> None:
    assert xp_for_level(level) == xp
    assert level_for_xp(xp) == level, "the level starts exactly at its experience"
    if level > 1:
        assert level_for_xp(xp - 1) == level - 1, "one point short is the level below"


def test_levels_stop_at_99_and_never_go_below_1() -> None:
    assert level_for_xp(200_000_000) == MAX_LEVEL
    assert level_for_xp(-5) == 1 and level_for_xp(0) == 1
    assert xp_for_level(120) == xp_for_level(99) and xp_for_level(0) == 0


def test_tiers_spread_over_the_top_of_the_curve() -> None:
    assert [tier_for_level(n) for n in (1, 14, 15, 30, 45, 60, 75, 89, 90, 99)] == [
        "bronze",
        "bronze",
        "iron",
        "steel",
        "mithril",
        "adamant",
        "rune",
        "rune",
        "divine",
        "divine",
    ]


# ---- skills and prices ----


def test_every_built_in_agent_trains_its_own_skill_and_three_belong_to_the_instance() -> None:
    trainers = [s.trained_by for s in STATS if s.trained_by != INSTANCE]
    assert len(trainers) == len(set(trainers)) == 18, "one skill per built-in agent"
    assert [s.id for s in STATS if s.trained_by == INSTANCE] == ["slayer", "mining", "farming"]
    assert len({s.id for s in STATS}) == len(STATS) == 21
    from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

    assert set(trainers) == set(BUILTIN_SEED), "a built-in with no skill, or a skill for an agent that is not built in"


def test_what_each_event_is_worth() -> None:
    assert price(ev("merged", "developer")) == ("smithing", "developer", 90_000)
    assert price(ev("qa_passed", "qa")) == ("herblore", "qa", 60_000)
    assert price(ev("run", "review")) == ("defence", "review", 30_000)
    assert price(ev("run", "triage")) == ("hunter", "triage", 2_000)
    assert price(ev("healed")) == ("slayer", None, 120_000)
    assert price(ev("watched")) == ("mining", None, 3_000)
    assert price(ev("scheduled")) == ("farming", None, 4_000)
    assert price(ev("run", None)) is None and price(ev("unknown", "developer")) is None


def test_a_context_audit_earns_more_for_every_outdated_claim_it_found() -> None:
    report = "I'll audit the files first.\nStale: 16\nFiles: 6\n\n## Findings\n"
    assert price(ev("audited", "context-auditor", report=report)) == ("fletching", "context-auditor", 40_000 + 16_000)
    assert price(ev("audited", "context-auditor", report="cut short")) == ("fletching", "context-auditor", 40_000)


def test_a_custom_agent_trains_magic_at_the_analysis_rate() -> None:
    assert stat_of_agent("my-reviewer") == "magic"
    assert price(ev("run", "my-reviewer")) == ("magic", "my-reviewer", progression.RUN_XP["analysis"])


# ---- adding up ----


def test_levels_add_up_from_history_with_every_level_crossed_recorded() -> None:
    events = [ev("merged", "developer", minutes=i) for i in range(3)] + [ev("healed", minutes=10)]
    result = compute(reversed(events), ["developer", "review"])
    assert result.stat_xp["smithing"] == 270_000 and result.agent_xp["developer"] == 270_000
    assert result.agent_xp["review"] == 0, "a defined agent with no work is listed at level 1"
    assert result.stat_xp["slayer"] == 120_000
    smithing = [u for u in result.level_ups if u.stat == "smithing"]
    assert [u.level for u in smithing] == [level_for_xp(90_000), level_for_xp(180_000), level_for_xp(270_000)]
    assert all(u.agent_type == "developer" for u in smithing) and smithing[0].at == T0, "time order, not row order"
    body = result.to_dict()
    assert body["total_level"] == level_for_xp(270_000) + level_for_xp(120_000) + (len(STATS) - 2)
    assert body["recent"][0]["stat"] == "slayer", "newest first"
    developer = next(a for a in body["agents"] if a["agent_type"] == "developer")
    assert developer == {
        "agent_type": "developer",
        "stat": "smithing",
        "xp": 270_000,
        "level": level_for_xp(270_000),
        "xp_to_next": xp_for_level(level_for_xp(270_000) + 1) - 270_000,
        "tier": tier_for_level(level_for_xp(270_000)),
    }
    assert {s["stat"]: s["trained_by"] for s in body["stats"]}["smithing"] == "developer"


def test_a_maxed_skill_has_nothing_left_to_the_next_level() -> None:
    body = compute([ev("watched", minutes=i) for i in range(5_000)]).to_dict()
    mining = next(s for s in body["stats"] if s["stat"] == "mining")
    assert mining["level"] == 99 and mining["xp_to_next"] is None


@pytest.mark.asyncio
async def test_the_service_computes_at_most_once_per_minute() -> None:
    class Repo:
        calls = 0

        async def events(self) -> list[ProgressEvent]:
            Repo.calls += 1
            return [ev("merged", "developer")]

        async def agent_types(self) -> list[str]:
            return ["developer"]

    now = [100.0]
    service = ProgressionService(Repo(), ttl_seconds=60, clock=lambda: now[0])
    first = await service.read()
    now[0] += 59
    assert await service.read() is first and Repo.calls == 1
    now[0] += 2
    await service.read()
    assert Repo.calls == 2
