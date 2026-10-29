"""Achievements (spec 2026-10-25, the achievement diary): what the account has achieved, never how busy it was.

An achievement is the active content pack's (#176): a stable ``id`` (it keys
the achievement for good), a name, a category, a flavour line, the metric
it counts with that metric's parameters, and its thresholds, one per tier
or a single one for an objective. Every metric adds up from rows the
database already holds, so like experience it is recomputed, never stored:
each tier's unlock time is the time of the step that crossed its
threshold, and the step that last moved a metric is its evidence. A secret
achievement shows as ``???`` until its first signal.

The metrics are code (``progression_vocabulary.METRICS``): the facts query
reads most of them, each with the pack's parameters bound as values, and the
growth metrics are read here from the level-ups. Every metric is an
outcome: a pull request that merged, a production error that stayed fixed,
an issue finished, a claim corrected, a change the gate stopped. Nothing
counts spend, runs or jobs started, which a busy and useless week would
win.

The shape follows the Hermes Agent achievements plugin (MIT): tiered
progress, discovered and secret entries, categories, a "what counts" line and
evidence, all derived from history.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from embry0.services import progression_templates as templates
from embry0.services.context_audit import report_counts
from embry0.services.progression import AgentLevelUp, LevelUp, RareDrop
from embry0.services.progression_pack import AchievementRule, Rules, fact_key
from embry0.storage.repositories.progression import Fact

#: What a reached objective reads as, in place of a tier: a value of the API, not a word people read.
COMPLETE = "complete"

#: The metrics with no SQL fragment, which :func:`metric_steps` reads from the level-ups and drops.
DERIVED_METRICS = frozenset({"best_total", "capes", "best_level", "rares", "skills_at_level", "characters_at_total"})


def _base(metric: str, rules: Rules) -> float:
    """A metric's value before any step: a character starts with every skill at 1, and at level 1."""
    if metric == "best_total":
        return float(len(rules.skill_ids))
    if metric == "best_level":
        return 1.0
    return 0.0


@dataclass(frozen=True)
class Step:
    """One step of a metric: ``value`` added at ``at``, and what did it (a pull request, a repo, an error)."""

    at: datetime
    value: float = 1.0
    label: str | None = None
    url: str | None = None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def metric_steps(
    facts: Iterable[Fact],
    level_ups: Sequence[LevelUp] = (),
    agent_level_ups: Sequence[AgentLevelUp] = (),
    drops: Sequence[RareDrop] = (),
    *,
    rules: Rules,
) -> dict[str, list[Step]]:
    """Every metric the pack counts, by fact key, as the steps it rose by, each with its time and evidence, oldest first."""
    steps: dict[str, list[Step]] = defaultdict(list)
    for fact in facts:
        if fact.metric == "audit":
            # A context audit that finished its work: its outdated claims, or, with none, a clean audit.
            stale, files = report_counts(fact.report)
            if stale:
                steps["stale_claims"].append(Step(fact.at, stale, fact.label, fact.url))
            elif files:
                steps["clean_audit"].append(Step(fact.at, 1, fact.label, fact.url))
            continue
        steps[fact.metric].append(Step(fact.at, fact.value, fact.label, fact.url))
    # From the level-ups: the highest total level of any character, the capes, and each level and total asked for.
    skill_levels = sorted(
        {int(a.entry.params["level"]) for a in rules.achievements if a.entry.metric == "skills_at_level"}
    )
    totals_asked = sorted(
        {int(a.entry.params["total"]) for a in rules.achievements if a.entry.metric == "characters_at_total"}
    )
    levels: dict[tuple[str, str], int] = {}
    totals: dict[str, int] = defaultdict(lambda: len(rules.skill_ids))
    best = len(rules.skill_ids)
    reached: dict[int, set[tuple[str, str]]] = {level: set() for level in skill_levels}
    for up in sorted(level_ups, key=lambda u: u.at):
        key = (up.agent_type, up.stat)
        before = totals[up.agent_type]
        totals[up.agent_type] += up.level - levels.get(key, 1)
        levels[key] = up.level
        if totals[up.agent_type] > best:
            steps["best_total"].append(Step(up.at, totals[up.agent_type] - best, up.agent_type))
            best = totals[up.agent_type]
        if up.level == rules.max_level:
            steps["capes"].append(Step(up.at, 1, f"{up.agent_type} · {up.stat}"))
        for level in skill_levels:
            if up.level >= level and key not in reached[level]:
                reached[level].add(key)
                steps[fact_key("skills_at_level", {"level": level})].append(
                    Step(up.at, 1, f"{up.agent_type} · {up.stat}")
                )
        for total in totals_asked:
            if before < total <= totals[up.agent_type]:
                steps[fact_key("characters_at_total", {"total": total})].append(Step(up.at, 1, up.agent_type))
    best_level = 1
    for agent_up in sorted(agent_level_ups, key=lambda u: u.at):
        if agent_up.level > best_level:
            steps["best_level"].append(Step(agent_up.at, agent_up.level - best_level, agent_up.agent_type))
            best_level = agent_up.level
    for drop in drops:
        steps["rares"].append(Step(drop.at, 1, f"{drop.agent_type} · {drop.rare}"))
    for series in steps.values():
        series.sort(key=lambda step: step.at)
    return steps


@dataclass
class AchievementState:
    achievement: AchievementRule
    value: float
    #: The pack's tier ids, lowest first: what each tier reached is called.
    tier_ids: tuple[str, ...]
    #: When each reached tier was crossed, the lowest first (an objective has at most one).
    tier_at: list[datetime] = field(default_factory=list)
    #: The step that last moved the metric.
    evidence: Step | None = None
    #: The metric's value before any step.
    base: float = 0.0

    @property
    def tier(self) -> int:
        """Tiers reached: 0 for none, every tier for the top; 1 for a complete objective."""
        return len(self.tier_at)

    @property
    def hidden(self) -> bool:
        return self.achievement.entry.secret and self.value <= self.base

    @property
    def tier_name(self) -> str | None:
        """The id of the highest tier reached, ``complete`` for an objective, None before the first."""
        if not self.tier:
            return None
        return COMPLETE if self.achievement.objective else self.tier_ids[self.tier - 1]

    def to_dict(self) -> dict[str, Any]:
        a = self.achievement.entry
        kind = "objective" if self.achievement.objective else "tiered"
        if self.hidden:
            return {
                "id": a.id,
                "name": "???",
                "category": a.category,
                "kind": kind,
                "icon": None,
                "what_counts": None,
                "flavour": None,
                "secret": True,
                "state": "secret",
                "tier": None,
                "tiers_reached": 0,
                "value": 0,
                "next_threshold": None,
                "thresholds": None,
                "unlocked_at": None,
                "latest_at": None,
                "evidence": None,
            }
        thresholds = a.thresholds
        return {
            "id": a.id,
            "name": a.name,
            "category": a.category,
            "kind": kind,
            "icon": a.icon,
            "what_counts": templates.render(a.what_counts, a.params),
            "flavour": a.flavour,
            "secret": a.secret,
            "state": "unlocked" if self.tier else "discovered",
            "tier": self.tier_name,
            "tiers_reached": self.tier,
            "value": int(self.value),
            "next_threshold": None if self.tier >= len(thresholds) else thresholds[self.tier],
            "thresholds": list(thresholds),
            "unlocked_at": _iso(self.tier_at[0]) if self.tier_at else None,
            "latest_at": _iso(self.tier_at[-1]) if self.tier_at else None,
            "evidence": None
            if self.evidence is None
            else {"label": self.evidence.label, "url": self.evidence.url, "at": _iso(self.evidence.at)},
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
    facts: Iterable[Fact],
    level_ups: Sequence[LevelUp] = (),
    agent_level_ups: Sequence[AgentLevelUp] = (),
    drops: Sequence[RareDrop] = (),
    *,
    rules: Rules,
) -> tuple[list[AchievementState], list[Unlock]]:
    """Each of the pack's achievements: its value, tiers and evidence, and every tier unlock in time order."""
    steps = metric_steps(facts, level_ups, agent_level_ups, drops, rules=rules)
    states: list[AchievementState] = []
    unlocks: list[Unlock] = []
    for achievement in rules.achievements:
        base = _base(achievement.entry.metric, rules)
        state = AchievementState(achievement, base, rules.tier_ids, base=base)
        thresholds = achievement.entry.thresholds
        for step in steps.get(achievement.key, []):
            state.value += step.value
            state.evidence = step
            while state.tier < len(thresholds) and state.value >= thresholds[state.tier]:
                state.tier_at.append(step.at)
                unlocks.append(Unlock(step.at, achievement.id, achievement.entry.name, state.tier_name or COMPLETE))
        states.append(state)
    unlocks.sort(key=lambda u: u.at)
    return states, unlocks
