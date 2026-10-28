"""Achievements (spec 2026-10-25, the achievement diary): outcomes only, tiered or objective, evidence, secrets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embry0.services.achievements import (
    CATALOGUE,
    CATEGORIES,
    COMPLETE,
    TIER_NAMES,
    evaluate,
    metric_steps,
)
from embry0.services.progression import STATS, AgentLevelUp, LevelUp, RareDrop, compute
from embry0.storage.repositories.progression import Fact, ProgressEvent

T0 = datetime(2026, 10, 1, tzinfo=UTC)

#: Ids an earlier catalogue unlocked with the same metric: they key unlocks, so they never change.
KEPT_IDS = {
    "forged_in_fire",
    "guardian_of_the_gate",
    "potion_master",
    "hunt_complete",
    "lines_straightened",
    "elder_agent",
    "mastery_cloak",
    "back_on_your_feet",
}
#: Metrics that counted activity, not outcomes: nothing may count them again.
NOISE = {"spent", "watched", "scheduled", "completed", "quick", "asked"}


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def by_id(states: list) -> dict:
    return {s.achievement.id: s for s in states}


def test_the_catalogue_is_fifty_outcomes_in_eight_categories_with_stable_ids() -> None:
    ids = [a.id for a in CATALOGUE]
    assert len(ids) == len(set(ids)) == 50
    assert KEPT_IDS <= set(ids)
    categories = [c.id for c in CATEGORIES]
    assert len(categories) == 8 and {a.category for a in CATALOGUE} == set(categories)
    assert not NOISE & {a.metric for a in CATALOGUE}, "spend, runs and jobs started are activity, not outcomes"
    for a in CATALOGUE:
        assert len(a.thresholds) in (1, len(TIER_NAMES)), a.id
        assert list(a.thresholds) == sorted(set(a.thresholds)), f"{a.id}: thresholds must rise"
        assert a.name and a.what_counts and a.flavour and a.icon, a.id
    assert TIER_NAMES == ("bronze", "silver", "gold", "platinum", "diamond", "obsidian", "divine")
    assert sum(a.secret for a in CATALOGUE) >= 5


def test_each_tier_unlocks_at_the_step_that_crossed_it_and_the_last_step_is_the_evidence() -> None:
    facts = [Fact("merged", at(i), 1, f"o/r #{i}", f"https://forge/o/r/pull/{i}") for i in range(16)]
    states, unlocks = evaluate(facts)
    forged = by_id(states)["forged_in_fire"]
    assert forged.value == 16 and forged.tier == 3, "1, 5 and 15 merges: Bronze, Silver, Gold"
    assert forged.tier_at == [at(0), at(4), at(14)]
    body = forged.to_dict()
    assert body["state"] == "unlocked" and body["tier"] == "gold" and body["next_threshold"] == 30
    assert body["category"] == "shipping" and body["kind"] == "tiered" and body["icon"] == "anvil"
    assert body["unlocked_at"] == at(0).isoformat() and body["latest_at"] == at(14).isoformat()
    assert body["evidence"] == {"label": "o/r #15", "url": "https://forge/o/r/pull/15", "at": at(15).isoformat()}
    assert [(u.tier, u.at) for u in unlocks if u.achievement_id == "forged_in_fire"] == [
        ("bronze", at(0)),
        ("silver", at(4)),
        ("gold", at(14)),
    ]


def test_an_audit_report_counts_its_claims_or_a_clean_audit() -> None:
    facts = [
        Fact("audit", at(5), 1, "o/r", report="Stale: 120\nFiles: 3\n"),
        Fact("audit", at(6), 1, "o/clean", report="Stale: 0\nFiles: 4\n"),
        Fact("audit", at(7), 1, "o/empty", report="Stale: 0\nFiles: 0\n"),
    ]
    s = by_id(evaluate(facts)[0])
    assert s["lines_straightened"].value == 120 and s["lines_straightened"].tier == 3
    assert s["lines_straightened"].tier_at == [at(5)] * 3, "one step can cross several tiers"
    assert s["weeded_garden"].value == 1 and s["weeded_garden"].evidence.label == "o/clean"


def test_an_objective_is_complete_once_and_has_no_tier() -> None:
    states, unlocks = evaluate([Fact("long_grind", at(3), 1, "o/r #9")])
    body = by_id(states)["long_grind"].to_dict()
    assert body["kind"] == "objective" and body["tier"] == COMPLETE and body["state"] == "unlocked"
    assert body["next_threshold"] is None and body["thresholds"] == [1]
    assert [(u.achievement_id, u.tier) for u in unlocks] == [("long_grind", COMPLETE)]


def test_a_secret_shows_its_category_and_nothing_else_until_its_first_signal() -> None:
    hidden = by_id(evaluate([])[0])["back_on_your_feet"].to_dict()
    assert hidden["name"] == "???" and hidden["state"] == "secret" and hidden["category"] == "grit"
    assert hidden["what_counts"] is None and hidden["flavour"] is None and hidden["icon"] is None
    assert hidden["evidence"] is None and hidden["thresholds"] is None
    seen = by_id(evaluate([Fact("comeback", at(9), 1, "o/r #2")])[0])["back_on_your_feet"].to_dict()
    assert seen["name"] == "Back on Your Feet" and seen["state"] == "unlocked" and seen["secret"] is True


def test_progress_metrics_come_from_levels_capes_agent_levels_and_rares() -> None:
    ups = [
        LevelUp(at(1), "developer", "coding", 60),
        LevelUp(at(2), "developer", "coding", 99),
        LevelUp(at(3), "review", "reviewing", 30),
        LevelUp(at(4), "review", "reading", 99),
        LevelUp(at(5), "review", "searching", 50),
    ]
    steps = metric_steps([], ups)
    # developer: 23 + 98 = 121 at minute 2; review overtakes at minute 5 with 23 + 29 + 98 + 49 = 199.
    assert sum(step.value for step in steps["best_total"]) == (23 + 29 + 98 + 49) - len(STATS)
    agent_ups = [AgentLevelUp(at(1), "developer", 12), AgentLevelUp(at(2), "review", 8)]
    drops = [RareDrop(at(6), "developer", "paper-crown")]
    s = by_id(evaluate([], ups, agent_ups, drops)[0])
    assert s["elder_agent"].value == 199, "the highest total of any one character, not the sum"
    assert s["mastery_cloak"].value == 2 and s["mastery_cloak"].tier_at == [at(2), at(4)]
    assert s["halfway_by_experience"].value == 2, "each skill counts once at 92, on its way to 99"
    assert s["fully_trained"].value == 12 and s["fully_trained"].tier == 1
    assert s["rare_finds"].value == 1 and s["rare_finds"].evidence.label == "developer · paper-crown"


def test_the_thousand_club_counts_each_character_once() -> None:
    ups = [LevelUp(at(i), "developer", stat.id, 99) for i, stat in enumerate(STATS[:12])]
    s = by_id(evaluate([], ups)[0])
    assert s["thousand_club"].value == 1 and s["thousand_club"].evidence.label == "developer"


def test_recent_mixes_levels_and_unlocks_newest_first() -> None:
    events = [ProgressEvent("merged", "developer", at(0))]
    progression = compute(events, [])
    progression.achievements, progression.unlocks = evaluate(
        [Fact("merged", at(0), 1, "o/r #1"), Fact("healed", at(30), 1, "Timeout")],
        progression.level_ups,
        progression.agent_level_ups,
    )
    recent = progression.to_dict()["recent"]
    assert [r["at"] for r in recent] == sorted((r["at"] for r in recent), reverse=True)
    assert {r["kind"] for r in recent} == {"level", "agent_level", "achievement"}
    first_unlock = next(r for r in recent if r["kind"] == "achievement" and r["id"] == "forged_in_fire")
    assert first_unlock == {
        "kind": "achievement",
        "at": at(0).isoformat(),
        "id": "forged_in_fire",
        "name": "Shipped It",
        "tier": "bronze",
    }
