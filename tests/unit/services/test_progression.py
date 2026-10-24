"""Progression (spec 2026-10-24): the classic curve, what each event earns its character, and the levels it adds up to."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embry0.services.progression import (
    ACTION_XP,
    MAX_LEVEL,
    STATS,
    Character,
    ProgressionService,
    compute,
    level_for_xp,
    price,
    tier_for_level,
    xp_for_level,
)
from embry0.storage.repositories.progression import ProgressEvent

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def ev(
    source: str,
    agent: str | None = None,
    minutes: int = 0,
    report: str | None = None,
    *,
    tools: dict[str, int] | None = None,
    actions: dict[str, int] | None = None,
) -> ProgressEvent:
    return ProgressEvent(source, agent, T0 + timedelta(minutes=minutes), report, tools, actions)


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


# ---- skills ----


def test_twenty_three_skills_in_four_groups_each_echoing_a_different_classic_skill() -> None:
    assert len(STATS) == len({s.id for s in STATS}) == 23
    assert len({s.echo for s in STATS}) == 23, "every classic skill is echoed exactly once"
    assert {s.group for s in STATS} == {"build", "verify", "investigate", "care"}


# ---- prices ----


def test_a_run_earns_each_tool_call_in_the_skill_it_trains() -> None:
    run = ev("run", "developer", actions={"edit:coding": 2, "shell:testing": 3, "read:reading": 1, "bogus:coding": 9})
    assert dict(price(run)) == {
        "coding": 2 * ACTION_XP["edit"],
        "testing": 3 * ACTION_XP["shell"],
        "reading": ACTION_XP["read"],
    }, "an unknown kind earns nothing"


def test_a_run_from_before_skill_actions_reads_its_tool_names() -> None:
    old = ev("run", "developer", tools={"Bash": 2, "Edit": 1, "Skill": 5})
    assert dict(price(old)) == {"shell": 2 * ACTION_XP["shell"], "coding": ACTION_XP["edit"]}
    recorded = ev("run", "developer", tools={"Bash": 2}, actions={"shell:version_control": 2})
    assert dict(price(recorded)) == {"version_control": 2 * ACTION_XP["shell"]}, "recorded actions win"


def test_some_agents_finished_run_is_an_outcome_on_top_of_its_calls() -> None:
    assert dict(price(ev("run", "review", actions={"read:reading": 1}))) == {
        "reading": ACTION_XP["read"],
        "reviewing": 30_000,
    }
    assert price(ev("run", "triage")) == [("triage", 2_000)]
    assert price(ev("run", "blast-radius")) == [("security", 40_000)]
    assert price(ev("run", "my-custom-agent")) == [], "a custom agent earns from its calls only"


def test_what_each_outcome_is_worth() -> None:
    assert price(ev("merged", "developer")) == [("coding", 60_000)]
    assert price(ev("opened", "developer")) == [("version_control", 20_000)]
    assert price(ev("qa_passed", "qa")) == [("quality_assurance", 50_000)]
    assert price(ev("asked", "triage")) == [("communication", 20_000)]
    assert price(ev("comeback", "developer")) == [("recovery", 90_000)]
    assert price(ev("resumed", "developer")) == [("recovery", 90_000)]
    assert price(ev("unknown", "developer")) == []


def test_work_no_character_does_earns_no_levels() -> None:
    for source in ("healed", "watched", "scheduled"):
        assert price(ev(source)) == []
    assert price(ev("resumed", None)) == [], "a resumed job with no run has no character to credit"


def test_a_context_audit_earns_more_for_every_outdated_claim_it_found() -> None:
    report = "I'll audit the files first.\nStale: 16\nFiles: 6\n\n## Findings\n"
    assert price(ev("audited", "context-auditor", report=report)) == [("documentation", 40_000 + 16_000)]
    assert price(ev("audited", "context-auditor", report="cut short")) == [("documentation", 40_000)]


# ---- characters ----


def test_a_character_totals_its_skills_and_wears_the_tier_of_its_best() -> None:
    fresh = Character("review")
    assert fresh.total_level == 23 and fresh.best_skill == "coding" and fresh.tier == "bronze"
    character = Character("developer", {"coding": xp_for_level(80), "testing": xp_for_level(80) + 5, "shell": 500})
    assert character.total_level == 80 + 80 + level_for_xp(500) + 20
    assert character.best_skill == "testing", "a level tie goes to the most experience"
    assert character.tier == "rune"


def test_levels_add_up_per_character_with_every_level_crossed_recorded() -> None:
    events = [ev("merged", "developer", minutes=i) for i in range(3)] + [ev("healed", minutes=10)]
    result = compute(reversed(events), ["developer", "review"])
    assert set(result.characters) == {"developer", "review"}, "a defined agent with no work is a character at 1"
    assert result.characters["developer"].stat_xp == {"coding": 180_000}
    coding = [u for u in result.level_ups if u.stat == "coding"]
    assert [u.level for u in coding] == [level_for_xp(60_000), level_for_xp(120_000), level_for_xp(180_000)]
    assert all(u.agent_type == "developer" for u in coding) and coding[0].at == T0, "time order, not row order"
    body = result.to_dict()
    assert body["account"] == {
        "xp": 180_000,
        "total_level": result.characters["developer"].total_level + 23,
        "characters": 2,
    }
    assert [s["id"] for s in body["skills"]] == [s.id for s in STATS]
    developer = next(c for c in body["characters"] if c["agent_type"] == "developer")
    level = level_for_xp(180_000)
    assert developer["total_level"] == level + 22 and developer["best_skill"] == "coding"
    assert developer["tier"] == tier_for_level(level) and len(developer["skills"]) == 23
    assert developer["skills"][0] == {
        "stat": "coding",
        "xp": 180_000,
        "level": level,
        "level_xp": xp_for_level(level),
        "xp_to_next": xp_for_level(level + 1) - 180_000,
    }
    assert body["recent"][0] == {
        "kind": "level",
        "at": (T0 + timedelta(minutes=2)).isoformat(),
        "agent_type": "developer",
        "stat": "coding",
        "level": level,
    }


def test_two_custom_agents_level_apart() -> None:
    events = [ev("run", "mine-a", actions={"read:reading": 50}), ev("run", "mine-b", actions={"read:reading": 1})]
    result = compute(events, [])
    assert result.characters["mine-a"].level("reading") > result.characters["mine-b"].level("reading")


def test_a_maxed_skill_has_nothing_left_to_the_next_level() -> None:
    body = compute([ev("merged", "developer", minutes=i) for i in range(250)]).to_dict()
    coding = body["characters"][0]["skills"][0]
    assert coding["level"] == 99 and coding["xp_to_next"] is None


@pytest.mark.asyncio
async def test_the_service_computes_at_most_once_per_minute() -> None:
    class Repo:
        calls = 0

        async def events(self) -> list[ProgressEvent]:
            Repo.calls += 1
            return [ev("merged", "developer")]

        async def agent_types(self) -> list[str]:
            return ["developer"]

        async def facts(self) -> list:
            return []

    now = [100.0]
    service = ProgressionService(Repo(), ttl_seconds=60, clock=lambda: now[0])
    first = await service.read()
    now[0] += 59
    assert await service.read() is first and Repo.calls == 1
    now[0] += 2
    await service.read()
    assert Repo.calls == 2
