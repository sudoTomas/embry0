"""Achievements (spec 2026-10-24): what the account has done, in seven tiers from Bronze to Divine.

An achievement is data: a stable ``id`` (it keys every unlock forever), a
name in the old-school game's voice, the metric it counts and seven
thresholds, one per tier. Its metric adds up from rows the database already
holds, so like experience it is recomputed, never stored: each tier's unlock
time is the time of the event that crossed its threshold. A secret
achievement shows as ``???`` until its first signal. Every achievement
counts across the whole account: all its characters, and the work no
character does.

The shape follows the Hermes Agent achievements plugin (MIT): tiered
progress, discovered and secret entries, a "what counts" line, all derived
from history.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from embry0.services.context_audit import report_counts
from embry0.services.progression import MAX_LEVEL, STATS, LevelUp
from embry0.storage.repositories.progression import Fact, ProgressEvent

TIER_NAMES: tuple[str, ...] = ("bronze", "iron", "steel", "mithril", "adamant", "rune", "divine")


@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    what_counts: str
    metric: str
    #: The metric's value for Bronze through Divine.
    thresholds: tuple[float, float, float, float, float, float, float]
    secret: bool = False


CATALOGUE: tuple[Achievement, ...] = (
    Achievement(
        "fresh_off_the_tutorial",
        "Fresh off the Tutorial",
        "Jobs that finished their work",
        "completed",
        (1, 25, 100, 250, 1_000, 2_500, 5_000),
    ),
    Achievement(
        "forged_in_fire",
        "Forged in Fire",
        "Pull requests merged",
        "merged",
        (1, 5, 15, 30, 60, 120, 250),
    ),
    Achievement(
        "guardian_of_the_gate",
        "Guardian of the Gate",
        "Reviews that finished",
        "reviews",
        (1, 10, 25, 50, 100, 250, 500),
    ),
    Achievement(
        "potion_master",
        "Potion Master",
        "QA jobs that completed",
        "qa_passed",
        (1, 5, 15, 30, 60, 120, 250),
    ),
    Achievement(
        "slayer_task_complete",
        "Slayer Task Complete",
        "Healing cases judged healed",
        "healed",
        (1, 3, 10, 25, 50, 100, 200),
    ),
    Achievement(
        "prospector",
        "Prospector",
        "Watcher runs that read logs",
        "watched",
        (10, 100, 500, 1_000, 2_500, 5_000, 10_000),
    ),
    Achievement(
        "green_thumb",
        "Green Thumb",
        "Audits the schedules started",
        "scheduled",
        (1, 10, 25, 50, 100, 250, 500),
    ),
    Achievement(
        "lines_straightened",
        "Lines Straightened",
        "Outdated claims the context audit found",
        "stale_claims",
        (10, 50, 100, 250, 500, 1_000, 2_500),
    ),
    Achievement(
        "bank_value",
        "Bank Value",
        "USD the jobs spent",
        "spent",
        (10, 50, 100, 250, 500, 1_000, 2_500),
    ),
    Achievement(
        "tick_perfect",
        "Tick Perfect",
        "Jobs that finished within 15 seconds of starting",
        "quick",
        (1, 5, 10, 25, 50, 100, 250),
    ),
    Achievement(
        "wise_old_agent",
        "Wise Old Agent",
        "The highest total level of any character",
        "best_total",
        (100, 250, 500, 750, 1_000, 1_500, 2_000),
    ),
    Achievement(
        "skill_cape",
        "Skill Cape",
        "Skill capes earned across every character",
        "capes",
        (1, 2, 5, 10, 23, 50, 100),
    ),
    Achievement(
        "oh_dear_you_are_dead",
        "Oh Dear, You Are Dead",
        "Merges on an issue whose earlier job failed",
        "comeback",
        (1, 3, 5, 10, 25, 50, 100),
        secret=True,
    ),
    Achievement(
        "click_here_to_continue",
        "Click Here to Continue",
        "Questions an agent asked a person",
        "asked",
        (1, 5, 10, 25, 50, 100, 250),
    ),
)

#: A metric's value before any event: a character starts with every skill at 1.
_BASE: dict[str, float] = {"best_total": float(len(STATS))}

#: The events whose count is a metric of the same name.
_COUNTED = frozenset({"merged", "qa_passed", "healed", "watched", "scheduled", "comeback", "asked"})

#: The agents whose finished run is a review.
_REVIEWERS = frozenset({"review", "pr-reviewer"})


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def metric_steps(
    events: Iterable[ProgressEvent], facts: Iterable[Fact], level_ups: Sequence[LevelUp]
) -> dict[str, list[tuple[datetime, float]]]:
    """Every metric as the steps it rose by, each with its time, oldest first."""
    steps: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for event in events:
        if event.source in _COUNTED:
            steps[event.source].append((event.at, 1))
        elif event.source == "run" and event.agent_type in _REVIEWERS:
            steps["reviews"].append((event.at, 1))
        elif event.source == "audited":
            stale, _files = report_counts(event.report)
            if stale:
                steps["stale_claims"].append((event.at, stale))
    for fact in facts:
        steps[fact.metric].append((fact.at, fact.value))
    # The highest total level of any character, stepping as its skills level.
    levels: dict[tuple[str, str], int] = {}
    totals: dict[str, int] = defaultdict(lambda: len(STATS))
    best = len(STATS)
    for up in sorted(level_ups, key=lambda u: u.at):
        key = (up.agent_type, up.stat)
        totals[up.agent_type] += up.level - levels.get(key, 1)
        levels[key] = up.level
        if totals[up.agent_type] > best:
            steps["best_total"].append((up.at, totals[up.agent_type] - best))
            best = totals[up.agent_type]
        if up.level == MAX_LEVEL:
            steps["capes"].append((up.at, 1))
    for series in steps.values():
        series.sort(key=lambda step: step[0])
    return steps


@dataclass
class AchievementState:
    achievement: Achievement
    value: float
    #: When each reached tier was crossed, Bronze first.
    tier_at: list[datetime] = field(default_factory=list)

    @property
    def tier(self) -> int:
        """Tiers reached: 0 for none, 7 for Divine."""
        return len(self.tier_at)

    @property
    def hidden(self) -> bool:
        return self.achievement.secret and self.value <= 0

    def to_dict(self) -> dict[str, Any]:
        a = self.achievement
        if self.hidden:
            return {
                "id": a.id,
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
        money = a.metric == "spent"
        return {
            "id": a.id,
            "name": a.name,
            "what_counts": a.what_counts,
            "secret": a.secret,
            "state": "unlocked" if self.tier else "discovered",
            "tier": TIER_NAMES[self.tier - 1] if self.tier else None,
            "tiers_reached": self.tier,
            "value": round(self.value, 2) if money else int(self.value),
            "next_threshold": None if self.tier >= len(TIER_NAMES) else a.thresholds[self.tier],
            "thresholds": list(a.thresholds),
            "unlocked_at": _iso(self.tier_at[0]) if self.tier_at else None,
        }


@dataclass(frozen=True)
class Unlock:
    at: datetime
    achievement_id: str
    name: str
    tier: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "achievement",
            "at": _iso(self.at),
            "id": self.achievement_id,
            "name": self.name,
            "tier": self.tier,
        }


def evaluate(
    events: Iterable[ProgressEvent], facts: Iterable[Fact], level_ups: Sequence[LevelUp]
) -> tuple[list[AchievementState], list[Unlock]]:
    """Each achievement's value and tiers, and every tier unlock in time order."""
    steps = metric_steps(events, facts, level_ups)
    states: list[AchievementState] = []
    unlocks: list[Unlock] = []
    for achievement in CATALOGUE:
        state = AchievementState(achievement, _BASE.get(achievement.metric, 0.0))
        for at, value in steps.get(achievement.metric, []):
            state.value += value
            while state.tier < len(TIER_NAMES) and state.value >= achievement.thresholds[state.tier]:
                state.tier_at.append(at)
                unlocks.append(Unlock(at, achievement.id, achievement.name, TIER_NAMES[state.tier - 1]))
        states.append(state)
    unlocks.sort(key=lambda u: u.at)
    return states, unlocks
