"""Achievements (spec 2026-10-24): account-wide, seven tiers per entry, unlock times from history, secrets hidden until seen."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embry0.services.achievements import CATALOGUE, TIER_NAMES, evaluate, metric_steps
from embry0.services.progression import STATS, LevelUp, compute
from embry0.storage.repositories.progression import Fact, ProgressEvent

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def by_id(states: list) -> dict:
    return {s.achievement.id: s for s in states}


def test_the_catalogue_has_stable_unique_ids_and_seven_rising_thresholds() -> None:
    ids = [a.id for a in CATALOGUE]
    assert len(ids) == len(set(ids)) == 14
    for a in CATALOGUE:
        assert len(a.thresholds) == len(TIER_NAMES) == 7, a.id
        assert list(a.thresholds) == sorted(set(a.thresholds)), f"{a.id}: thresholds must rise"
    assert TIER_NAMES[0] == "bronze" and TIER_NAMES[-1] == "divine"


def test_each_tier_unlocks_at_the_event_that_crossed_it() -> None:
    events = [ProgressEvent("merged", "developer", at(i)) for i in range(16)]
    states, unlocks = evaluate(events, [], [])
    forged = by_id(states)["forged_in_fire"]
    assert forged.value == 16 and forged.tier == 3, "1, 5 and 15 merges: Bronze, Iron, Steel"
    assert forged.tier_at == [at(0), at(4), at(14)]
    body = forged.to_dict()
    assert body["state"] == "unlocked" and body["tier"] == "steel" and body["next_threshold"] == 30
    assert body["unlocked_at"] == at(0).isoformat()
    assert [(u.tier, u.at) for u in unlocks if u.achievement_id == "forged_in_fire"] == [
        ("bronze", at(0)),
        ("iron", at(4)),
        ("steel", at(14)),
    ]


def test_one_step_can_cross_several_tiers_and_a_report_counts_its_claims() -> None:
    report = "Stale: 120\nFiles: 3\n"
    states, unlocks = evaluate([ProgressEvent("audited", "context-auditor", at(5), report)], [], [])
    lines = by_id(states)["lines_straightened"]
    assert lines.value == 120 and lines.tier == 3 and lines.tier_at == [at(5)] * 3
    assert [u.tier for u in unlocks] == ["bronze", "iron", "steel"]


def test_facts_count_and_money_keeps_its_cents() -> None:
    facts = [Fact("spent", at(1), 7.256), Fact("spent", at(2), 5.0), Fact("quick", at(3))]
    states, _ = evaluate([ProgressEvent("asked", "triage", at(4))], facts, [])
    s = by_id(states)
    assert s["bank_value"].to_dict()["value"] == 12.26 and s["bank_value"].tier == 1
    assert s["tick_perfect"].tier == 1 and s["click_here_to_continue"].tier == 1
    assert s["fresh_off_the_tutorial"].to_dict()["state"] == "discovered", (
        "known, with its progress, before its first tier"
    )


def test_wise_old_agent_follows_the_best_character_and_capes_count_across_characters() -> None:
    ups = [
        LevelUp(at(1), "developer", "coding", 60),
        LevelUp(at(2), "developer", "coding", 99),
        LevelUp(at(3), "review", "reviewing", 30),
        LevelUp(at(4), "review", "reading", 99),
        LevelUp(at(5), "review", "searching", 50),
    ]
    steps = metric_steps([], [], ups)
    # developer: 23 + 98 = 121 at minute 2; review overtakes at minute 5 with 23 + 29 + 98 + 49 = 199.
    assert sum(v for _t, v in steps["best_total"]) == (23 + 29 + 98 + 49) - len(STATS)
    states, _ = evaluate([], [], ups)
    s = by_id(states)
    assert s["wise_old_agent"].value == 199, "the highest total of any one character, not the sum"
    assert s["skill_cape"].value == 2 and s["skill_cape"].tier_at == [at(2), at(4)]


def test_work_no_character_does_still_counts_for_the_account() -> None:
    events = [ProgressEvent("watched", None, at(i)) for i in range(10)] + [ProgressEvent("scheduled", None, at(11))]
    events.append(ProgressEvent("healed", None, at(12)))
    s = by_id(evaluate(events, [], [])[0])
    assert s["prospector"].tier == 1 and s["green_thumb"].tier == 1 and s["slayer_task_complete"].tier == 1


def test_reviews_count_both_reviewers() -> None:
    events = [ProgressEvent("run", "review", at(1)), ProgressEvent("run", "pr-reviewer", at(2))]
    events.append(ProgressEvent("run", "developer", at(3)))
    assert by_id(evaluate(events, [], [])[0])["guardian_of_the_gate"].value == 2


def test_a_secret_shows_nothing_until_its_first_signal() -> None:
    states, _ = evaluate([], [], [])
    hidden = by_id(states)["oh_dear_you_are_dead"].to_dict()
    assert hidden == {
        "id": "oh_dear_you_are_dead",
        "name": "???",
        "what_counts": None,
        "secret": True,
        "state": "secret",
        "tier": None,
        "tiers_reached": 0,
        "value": 0,
        "next_threshold": None,
        "thresholds": None,
        "unlocked_at": None,
    }
    states, _ = evaluate([ProgressEvent("comeback", "developer", at(9))], [], [])
    seen = by_id(states)["oh_dear_you_are_dead"].to_dict()
    assert seen["name"] == "Oh Dear, You Are Dead" and seen["state"] == "unlocked" and seen["secret"] is True


def test_recent_mixes_levels_and_unlocks_newest_first() -> None:
    events = [ProgressEvent("merged", "developer", at(0)), ProgressEvent("healed", None, at(30))]
    progression = compute(events, [])
    progression.achievements, progression.unlocks = evaluate(events, [], progression.level_ups)
    recent = progression.to_dict()["recent"]
    assert [r["at"] for r in recent] == sorted((r["at"] for r in recent), reverse=True)
    kinds = {r["kind"] for r in recent}
    assert kinds == {"level", "agent_level", "achievement"}
    first_unlock = next(r for r in recent if r["kind"] == "achievement" and r["id"] == "forged_in_fire")
    assert first_unlock == {
        "kind": "achievement",
        "at": at(0).isoformat(),
        "id": "forged_in_fire",
        "name": "Forged in Fire",
        "tier": "bronze",
    }
