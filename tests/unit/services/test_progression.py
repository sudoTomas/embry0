"""Progression (spec 2026-10-24): the classic curve, what each event earns its character, and the levels it adds up to."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
import yaml

from embry0.services.progression import Character, ProgressionService, compute, first_total, price
from embry0.services.progression_pack import (
    Rules,
    ShippedPack,
    default_pack,
    default_rules,
    default_text,
    pack_hash,
    parse,
    rules_for,
)
from embry0.services.quests import Reward
from embry0.storage.repositories.progression import JobRow, Journal, ProgressEvent

T0 = datetime(2026, 10, 1, tzinfo=UTC)
RULES = default_rules()
ACTION_XP = RULES.action_xp
MAX_LEVEL = RULES.max_level
SKILLS = RULES.skill_ids
xp_for_level = RULES.xp_for_level
level_for_xp = RULES.level_for_xp


def tier_for_level(level: int) -> str:
    return RULES.tier_for_level(level).id


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
        "silver",
        "gold",
        "platinum",
        "diamond",
        "obsidian",
        "obsidian",
        "divine",
        "divine",
    ]


# ---- skills ----


def test_twenty_three_skills_in_four_groups() -> None:
    skills = RULES.pack.skills
    assert len(skills) == len({s.id for s in skills}) == 23
    assert {s.group for s in skills} == {"build", "verify", "investigate", "care"}


# ---- prices ----


def test_a_run_earns_each_tool_call_in_the_skill_it_trains() -> None:
    run = ev("run", "developer", actions={"edit:coding": 2, "shell:testing": 3, "read:reading": 1, "bogus:coding": 9})
    assert dict(price(run, rules=RULES)) == {
        "coding": 2 * ACTION_XP["edit"],
        "testing": 3 * ACTION_XP["shell"],
        "reading": ACTION_XP["read"],
    }, "an unknown kind earns nothing"


def test_a_run_from_before_skill_actions_reads_its_tool_names() -> None:
    old = ev("run", "developer", tools={"Bash": 2, "Edit": 1, "Skill": 5})
    assert dict(price(old, rules=RULES)) == {"shell": 2 * ACTION_XP["shell"], "coding": ACTION_XP["edit"]}
    recorded = ev("run", "developer", tools={"Bash": 2}, actions={"shell:version_control": 2})
    assert dict(price(recorded, rules=RULES)) == {"version_control": 2 * ACTION_XP["shell"]}, "recorded actions win"


def test_some_agents_finished_run_is_an_outcome_on_top_of_its_calls() -> None:
    assert dict(price(ev("run", "review", actions={"read:reading": 1}), rules=RULES)) == {
        "reading": ACTION_XP["read"],
        "reviewing": 30_000,
    }
    assert price(ev("run", "triage"), rules=RULES) == [("triage", 2_000)]
    assert price(ev("run", "blast-radius"), rules=RULES) == [("security", 40_000)]
    assert price(ev("run", "my-custom-agent"), rules=RULES) == [], "a custom agent earns from its calls only"


def test_what_each_outcome_is_worth() -> None:
    assert price(ev("merged", "developer"), rules=RULES) == [("coding", 60_000)]
    assert price(ev("opened", "developer"), rules=RULES) == [("version_control", 20_000)]
    assert price(ev("qa_passed", "qa"), rules=RULES) == [("quality_assurance", 50_000)]
    assert price(ev("asked", "triage"), rules=RULES) == [("communication", 20_000)]
    assert price(ev("comeback", "developer"), rules=RULES) == [("recovery", 90_000)]
    assert price(ev("resumed", "developer"), rules=RULES) == [("recovery", 90_000)]
    assert price(ev("unknown", "developer"), rules=RULES) == []


def test_work_no_character_does_earns_no_levels() -> None:
    for source in ("healed", "watched", "scheduled"):
        assert price(ev(source), rules=RULES) == []
    assert price(ev("resumed", None), rules=RULES) == [], "a resumed job with no run has no character to credit"


def test_a_context_audit_earns_more_for_every_outdated_claim_it_found() -> None:
    report = "I'll audit the files first.\nStale: 16\nFiles: 6\n\n## Findings\n"
    assert price(ev("audited", "context-auditor", report=report), rules=RULES) == [("documentation", 40_000 + 16_000)]
    assert price(ev("audited", "context-auditor", report="cut short"), rules=RULES) == [("documentation", 40_000)]


def test_a_merged_cleanup_earns_its_base_and_its_net_lines_removed_up_to_the_cap() -> None:
    def cleaned(added: int, removed: int) -> list[tuple[str, int]]:
        return price(ev("cleaned", "slopmaster", report=json.dumps({"added": added, "removed": removed})), rules=RULES)

    assert cleaned(10, 110) == [("coding", 20_000 + 100 * 300)]
    assert cleaned(50, 10) == [("coding", 20_000)], "a cleanup that grew the code earns the base only"
    assert cleaned(0, 50_000) == [("coding", 20_000 + 1_000 * 300)], "lines past the cap earn nothing more"


# ---- characters ----


def test_a_character_totals_its_skills_and_wears_the_tier_of_its_best() -> None:
    fresh = Character("review", rules=RULES)
    assert fresh.total_level == 23 and fresh.best_skill == "coding" and fresh.tier == "bronze"
    character = Character(
        "developer", {"coding": xp_for_level(80), "testing": xp_for_level(80) + 5, "shell": 500}, rules=RULES
    )
    assert character.total_level == 80 + 80 + level_for_xp(500) + 20
    assert character.best_skill == "testing", "a level tie goes to the most experience"
    assert character.tier == "obsidian"


def test_levels_add_up_per_character_with_every_level_crossed_recorded() -> None:
    events = [ev("merged", "developer", minutes=i) for i in range(3)] + [ev("healed", minutes=10)]
    result = compute(reversed(events), ["developer", "review"], rules=RULES)
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
        "quest_points": 0,
        "cloak_trim": False,
        "divine_trim": False,
    }
    assert body["quests"] == [] and body["diaries"] == [], "set by the service"
    assert [s["id"] for s in body["skills"]] == list(SKILLS)
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
    result = compute(events, [], rules=RULES)
    assert result.characters["mine-a"].level("reading") > result.characters["mine-b"].level("reading")


def test_a_maxed_skill_has_nothing_left_to_the_next_level() -> None:
    body = compute([ev("merged", "developer", minutes=i) for i in range(250)], rules=RULES).to_dict()
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

        async def facts(self, queries: object, timezone: str) -> list:
            return []

        async def journal(self) -> Journal:
            return Journal(
                [
                    JobRow("o/app", "i-1", None, "failed", T0, T0),
                    JobRow("o/app", "i-1", None, "pr_merged", T0, T0 + timedelta(hours=1)),
                ],
                [],
            )

    now = [100.0]
    service = ProgressionService(Repo(), packs=ShippedPack(), ttl_seconds=60, clock=lambda: now[0])
    first = await service.read()
    assert [q.id for q in first.quests if q.completed_at] == ["try_again"]
    assert first.characters["developer"].stat_xp["recovery"] == 60_000, "the quest's XP reached the levels"
    assert [d.repo for d in first.diaries] == ["o/app"]
    now[0] += 59
    assert await service.read() is first and Repo.calls == 1
    now[0] += 2
    await service.read()
    assert Repo.calls == 2


# ---- what a character wears ----


def test_capes_list_the_99s_most_experience_first() -> None:
    character = Character("developer", {"coding": 14_000_000, "reading": 13_500_000, "shell": 2_000_000}, rules=RULES)
    assert character.capes == ["coding", "reading"]
    assert Character("review", rules=RULES).capes == []


def test_a_merge_on_the_holidays_drops_a_rare_once() -> None:
    christmas = datetime(2026, 12, 25, 15, tzinfo=UTC)
    events = [
        ProgressEvent("merged", "developer", christmas),
        ProgressEvent("merged", "developer", christmas + timedelta(minutes=5)),
        ProgressEvent("merged", "developer", datetime(2026, 10, 31, 23, 59, tzinfo=UTC)),
        ProgressEvent("merged", "developer", datetime(2026, 11, 1, 0, 1, tzinfo=UTC)),  # a day late in UTC
    ]
    result = compute(events, ["developer"], rules=RULES)
    assert set(result.characters["developer"].rares) == {"paper-crown", "harvest-mask"}
    assert [(d.rare, d.at) for d in result.drops] == [
        ("harvest-mask", datetime(2026, 10, 31, 23, 59, tzinfo=UTC)),
        ("paper-crown", christmas),
    ], "each rare once, when first earned"
    body = result.to_dict()["characters"][0]
    assert [r["id"] for r in body["rares"]] == ["harvest-mask", "paper-crown"]
    assert body["capes"] == []


def test_the_first_run_of_a_new_year_drops_a_golden_paper_crown() -> None:
    events = [
        ProgressEvent("run", "triage", datetime(2026, 12, 31, 23, tzinfo=UTC)),
        ProgressEvent("run", "qa", datetime(2027, 1, 1, 0, 30, tzinfo=UTC)),
        ProgressEvent("run", "triage", datetime(2027, 1, 1, 1, tzinfo=UTC)),
    ]
    result = compute(events, [], rules=RULES)
    assert result.characters["qa"].rares == {"golden-paper-crown": datetime(2027, 1, 1, 0, 30, tzinfo=UTC)}
    assert result.characters["triage"].rares == {}, "the year's first run only, and never the very first run"
    recent = result.to_dict()["recent"]
    assert {"kind": "rare", "at": "2027-01-01T00:30:00+00:00", "agent_type": "qa", "id": "golden-paper-crown"} in recent


# ---- quests feed the levels ----


def test_a_quest_reward_adds_xp_at_its_completion() -> None:
    result = compute(
        [ev("merged", "developer")],
        ["developer"],
        [(T0 + timedelta(hours=2), Reward("qa", "quality_assurance", 50_000))],
        rules=RULES,
    )
    assert result.characters["qa"].stat_xp == {"quality_assurance": 50_000}
    assert result.level_ups[-1].at == T0 + timedelta(hours=2) and result.level_ups[-1].agent_type == "qa"


def test_first_total_is_when_a_character_reached_it() -> None:
    result = compute([ev("merged", "developer", minutes=i) for i in range(250)], ["developer"], rules=RULES)
    reached = first_total(result.level_ups, 23 + 98, rules=RULES)
    assert reached is not None and result.characters["developer"].level("coding") == 99
    assert first_total(result.level_ups, 2_000, rules=RULES) is None


def test_every_quest_done_gives_the_highest_character_the_quest_cloak() -> None:
    from embry0.services.quests import evaluate_quests

    result = compute([ev("merged", "developer")], ["developer", "review"], rules=RULES)
    result.quests = evaluate_quests(Journal([], []), [], lambda _total: None, rules=RULES)
    assert result.quest_cloak_owner is None
    for q in result.quests:
        q.steps = [type(s)(s.text, T0) for s in q.steps]
        q.requirement_met_at = T0
    assert result.quest_cloak_owner == "developer" and result.quest_points == 9 and result.cloak_trim
    body = result.to_dict()
    assert {c["agent_type"]: c["quest_cloak"] for c in body["characters"]} == {"developer": True, "review": False}
    assert {r["kind"] for r in body["recent"]} >= {"quest", "level"}


# ---- the agent's one level ----


def test_the_agent_level_is_the_combat_formula_over_its_best_skills() -> None:
    assert Character("fresh", rules=RULES).agent_level == 1
    maxed = Character("maxed", {s: xp_for_level(99) for s in SKILLS}, rules=RULES)
    assert maxed.agent_level == 126, "every skill at 99 is the formula's top"
    levels = {"coding": 84, "version_control": 75, "reading": 75, "shell": 74, "testing": 60}
    developer = Character("developer", {stat: xp_for_level(level) for stat, level in levels.items()}, rules=RULES)
    # floor(0.25 * (75 + 74 + 60 // 2) + 0.325 * (84 + 75))
    assert developer.agent_level == 96
    assert developer.to_dict()["level"] == 96


def test_the_agent_level_rises_as_its_skills_do_and_shows_in_recent() -> None:
    result = compute([ev("merged", "developer", minutes=i) for i in range(30)], ["developer"], rules=RULES)
    ups = [u for u in result.agent_level_ups if u.agent_type == "developer"]
    assert ups and [u.level for u in ups] == sorted({u.level for u in ups}), "each level once, rising"
    assert ups[-1].level == result.characters["developer"].agent_level
    recent = result.to_dict()["recent"]
    assert {"kind": "agent_level", "agent_type": "developer", "level": ups[-1].level}.items() <= next(
        r for r in recent if r["kind"] == "agent_level"
    ).items()


# ---- the content pack (#176) ----


def _pack(change: Callable[[dict[str, Any]], None]) -> Rules:
    data = yaml.safe_load(default_text())
    change(data)
    return rules_for(parse(data))


def test_todays_category_keys_and_older_skill_keys_price_the_same() -> None:
    older = ev("run", "developer", actions={"shell:testing": 3, "edit:documentation": 2, "read:reading": 4})
    current = ev("run", "developer", actions={"shell:test-runner": 3, "edit:docs": 2, "read:files": 4})
    assert price(older, rules=RULES) == price(current, rules=RULES) != []


def test_a_pack_that_renames_and_merges_skills_reprices_a_stored_run() -> None:
    def merge_verification(data: dict[str, Any]) -> None:
        skills = [s for s in data["skills"] if s["id"] not in ("testing", "linting")]
        skills.append(
            {
                "id": "verification",
                "name": "Verification",
                "group": "verify",
                "trained_by": ["shell:test-runner", "shell:linter", "edit:tests"],
            }
        )
        data["skills"] = skills

    rules = _pack(merge_verification)
    stored = ev("run", "developer", actions={"shell:testing": 3, "shell:linting": 2})  # written before #176
    assert price(stored, rules=RULES) == [("testing", 3 * 600), ("linting", 2 * 600)]
    assert price(stored, rules=rules) == [("verification", 5 * 600)], "the pack, not the stored key, decides the skill"
    body = compute([stored], ["developer"], rules=rules).to_dict()
    assert "verification" in [s["id"] for s in body["skills"]] and "testing" not in [s["id"] for s in body["skills"]]


def test_a_category_no_skill_names_earns_nothing() -> None:
    def unmap_shell(data: dict[str, Any]) -> None:
        next(s for s in data["skills"] if s["id"] == "shell")["trained_by"] = []

    assert price(ev("run", "developer", actions={"shell:plain": 9}), rules=_pack(unmap_shell)) == []


def test_rares_follow_the_packs_calendar() -> None:
    late_christmas_eve = datetime(2026, 12, 24, 23, 30, tzinfo=UTC)  # already 25 December in Berlin
    events = [ProgressEvent("merged", "developer", late_christmas_eve)]
    assert compute(events, rules=RULES).characters["developer"].rares == {}, "still 24 December in UTC"
    berlin = _pack(lambda d: d.update(timezone="Europe/Berlin"))
    assert compute(events, rules=berlin).characters["developer"].rares == {"paper-crown": late_christmas_eve}
    new_year_in_berlin = [
        ProgressEvent("run", "triage", datetime(2026, 12, 31, 20, tzinfo=UTC)),
        ProgressEvent("run", "qa", datetime(2026, 12, 31, 23, 30, tzinfo=UTC)),
    ]
    assert compute(new_year_in_berlin, ["qa", "triage"], rules=RULES).characters["qa"].rares == {}
    assert set(compute(new_year_in_berlin, ["qa", "triage"], rules=berlin).characters["qa"].rares) == {
        "golden-paper-crown"
    }


def test_a_short_pack_still_has_a_level_and_a_tier() -> None:
    def three_skills(data: dict[str, Any]) -> None:
        data["skills"] = data["skills"][:3]  # coding, version control, building
        data["tiers"] = data["tiers"][:2]
        data["rates"]["outcomes"] = {"merged": {"skill": "coding", "xp": 60_000}}
        data["rates"]["runs"] = []
        data["quests"] = []
        for a in data["achievements"]:
            a["thresholds"] = a["thresholds"][:2] if len(a["thresholds"]) > 1 else a["thresholds"]

    rules = _pack(three_skills)
    fresh = Character("x", rules=rules)
    assert fresh.agent_level == 1 and fresh.total_level == 3 and fresh.tier == "bronze"
    assert rules.character_max_level == rules.character_level([99, 99, 99, 1, 1])


def test_the_answer_carries_the_packs_names_colours_words_and_art() -> None:
    body = compute([], ["developer"], rules=RULES).to_dict()
    assert body["pack"] == {"hash": RULES.hash, "version": 1, "timezone": "UTC", "timezone_applied": "UTC"}
    assert [t["id"] for t in body["tiers"]] == ["bronze", "silver", "gold", "platinum", "diamond", "obsidian", "divine"]
    assert body["tiers"][0] == {"id": "bronze", "name": "Bronze", "from_level": 1, "color": "#9c6b2f", "gear": "bronze"}
    assert [r["id"] for r in body["rares"]] == ["golden-paper-crown", "paper-crown", "harvest-mask"], "in wear order"
    assert body["groups"][0] == {"id": "build", "name": "Build"}
    assert body["diary_tiers"][-1] == {"id": "elite", "name": "Elite"}
    assert body["messages"]["level"] == "{character} reached {skill} level {level}."
    assert body["labels"]["quest_journal"] == "Deeds" and body["cloaks"]["quest"]["base"] == "#2f4a8f"
    assert body["curve"] == {"max_level": 99, "character_max_level": 126}
    assert body["characters"][0]["gear"] == "bronze"


def test_tier_names_and_gear_come_from_the_pack() -> None:
    def renamed(data: dict[str, Any]) -> None:
        data["tiers"][0].update(id="tin", name="Tin", gear="silver")

    body = compute([], ["developer"], rules=_pack(renamed)).to_dict()
    assert body["characters"][0]["tier"] == "tin" and body["characters"][0]["gear"] == "silver"


@pytest.mark.asyncio
async def test_the_service_recomputes_at_once_when_the_pack_changes() -> None:
    class Repo:
        async def events(self) -> list[ProgressEvent]:
            return [ev("merged", "developer")]

        async def agent_types(self) -> list[str]:
            return ["developer"]

        async def facts(self, queries: object, timezone: str) -> list:
            return []

        async def journal(self) -> Journal:
            return Journal([], [])

    class Packs:
        pack = default_pack()

        async def active(self) -> Any:
            return Packs.pack

    service = ProgressionService(Repo(), packs=Packs(), ttl_seconds=600, clock=lambda: 0.0)
    first = await service.read()
    assert first.characters["developer"].stat_xp == {"coding": 60_000}
    data = yaml.safe_load(default_text())
    data["rates"]["outcomes"]["merged"]["xp"] = 70_000
    Packs.pack = parse(data)
    second = await service.read()
    assert second is not first and second.characters["developer"].stat_xp == {"coding": 70_000}, (
        "a changed pack is read at once, whatever the time to live"
    )
    assert (await service.rules()).hash == pack_hash(Packs.pack)


@pytest.mark.asyncio
async def test_a_timezone_the_database_refuses_falls_back_to_utc_and_a_real_failure_still_raises() -> None:
    """The pack's timezone is checked by Python and applied by Postgres (review of #207, finding 1d)."""

    class Repo:
        asked: list[str] = []
        broken = False

        async def events(self) -> list[ProgressEvent]:
            return [ev("merged", "developer")]

        async def agent_types(self) -> list[str]:
            return ["developer"]

        async def facts(self, queries: object, timezone: str) -> list:
            Repo.asked.append(timezone)
            if Repo.broken:
                raise RuntimeError("invalid value for parameter TimeZone")
            if timezone != "UTC":
                raise asyncpg.InvalidParameterValueError(f'time zone "{timezone}" not recognized')
            return []

        async def journal(self) -> Journal:
            return Journal([], [])

    class Packs:
        async def active(self) -> Any:
            data = yaml.safe_load(default_text())
            data["timezone"] = "Europe/Berlin"
            return parse(data)

    service = ProgressionService(Repo(), packs=Packs(), ttl_seconds=0)
    try:
        progression = await service.read()
    except RuntimeError as exc:
        raise AssertionError(f"a timezone Postgres refuses took down every read of the progression: {exc}") from exc
    assert Repo.asked == ["Europe/Berlin", "UTC"], "the facts are read again on UTC's calendar"
    assert progression.achievements, "the progression still answers"
    pack = progression.to_dict()["pack"]
    assert (pack["timezone"], pack.get("timezone_applied")) == ("Europe/Berlin", "UTC"), (
        "the answer must say its achievements were counted on UTC's calendar, not the pack's"
    )
    Repo.broken = True
    with pytest.raises(RuntimeError, match="TimeZone"):
        await ProgressionService(Repo(), packs=ShippedPack(), ttl_seconds=0).read()


async def test_only_a_timezone_postgres_refuses_falls_back_and_any_other_failure_is_raised() -> None:
    """A statement timeout under a non-UTC pack must fail the read, not be retried on UTC (review of #207).

    A silent retry would count the hot streak and the oddities on UTC's
    days for the next minute of cached answers, while the rares and
    ``pack.timezone`` say the pack's zone.
    """

    class Repo:
        asked: list[str] = []

        async def events(self) -> list[ProgressEvent]:
            return [ev("merged", "developer")]

        async def agent_types(self) -> list[str]:
            return ["developer"]

        async def facts(self, queries: object, timezone: str) -> list:
            Repo.asked.append(timezone)
            raise asyncpg.QueryCanceledError("canceling statement due to statement timeout")

        async def journal(self) -> Journal:
            return Journal([], [])

    class Packs:
        async def active(self) -> Any:
            data = yaml.safe_load(default_text())
            data["timezone"] = "Europe/Berlin"
            return parse(data)

    with pytest.raises(asyncpg.QueryCanceledError):
        await ProgressionService(Repo(), packs=Packs(), ttl_seconds=0).read()
    assert Repo.asked == ["Europe/Berlin"], "a failure that is not the time zone must not be retried on UTC"


async def test_the_answer_names_the_pack_timezone_when_it_applied() -> None:
    class Repo:
        async def events(self) -> list[ProgressEvent]:
            return []

        async def agent_types(self) -> list[str]:
            return ["developer"]

        async def facts(self, queries: object, timezone: str) -> list:
            return []

        async def journal(self) -> Journal:
            return Journal([], [])

    class Packs:
        async def active(self) -> Any:
            data = yaml.safe_load(default_text())
            data["timezone"] = "Europe/Berlin"
            return parse(data)

    pack = (await ProgressionService(Repo(), packs=Packs(), ttl_seconds=0).read()).to_dict()["pack"]
    assert pack.get("timezone_applied") == "Europe/Berlin", "with no fallback the pack's own calendar applied"
