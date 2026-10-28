"""Achievements (spec 2026-10-25, the achievement diary): what the account has achieved, never how busy it was.

An achievement is data: a stable ``id`` (it keys every unlock forever), a
name in an adventure game's voice that still says what it counts, a
category, a flavour line, the metric it counts and its thresholds. Most are
tiered, Bronze to Divine; an objective has one threshold and is simply
complete. Every metric adds up from rows the database already holds, so like
experience it is recomputed, never stored: each tier's unlock time is the time
of the step that crossed its threshold, and the step that last moved a metric
is its evidence. A secret achievement shows as ``???`` until its first signal.

Every metric is an outcome: a pull request that merged, a production error
that stayed fixed, an issue finished, a claim corrected, a change the gate
stopped. Nothing counts spend, runs or jobs started, which a busy and useless
week would win.

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

from embry0.services.context_audit import report_counts
from embry0.services.progression import MAX_LEVEL, STATS, AgentLevelUp, LevelUp, RareDrop
from embry0.storage.repositories.progression import Fact

TIER_NAMES: tuple[str, ...] = ("bronze", "silver", "gold", "platinum", "diamond", "obsidian", "divine")
#: What a reached objective reads as, in place of a tier.
COMPLETE = "complete"


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    blurb: str


CATEGORIES: tuple[Category, ...] = (
    Category("shipping", "Shipping", "Pull requests that merged, and how cleanly they got there."),
    Category("hunting", "Hunting", "Production errors the watcher found, and the fixes that kept them dead."),
    Category("questing", "Questing", "Issues finished, and the answers owners gave."),
    Category("lore", "Lore", "Instruction files, area maps and standards kept true."),
    Category("guarding", "Guarding", "Reviews, QA and the gate that stops a bad merge."),
    Category("grit", "Grit", "Work that failed, came back, and landed anyway."),
    Category("growth", "Growth", "What the characters grew into."),
    Category("oddities", "Oddities", "Strange hours and rare finds. Mostly secret."),
)


@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    category: str
    what_counts: str
    flavour: str
    metric: str
    #: The metric's value for Bronze through Divine, or one value for an objective.
    thresholds: tuple[float, ...]
    secret: bool = False
    #: The pixel icon the page draws (frontend/src/components/progression/achievementIcons.tsx).
    icon: str = "star"

    @property
    def objective(self) -> bool:
        return len(self.thresholds) == 1


_COUNTS = (1, 5, 10, 25, 50, 100, 200)
_RARE = (1, 2, 5, 10, 25, 50, 100)
_REPOS = (1, 2, 3, 5, 8, 13, 21)

CATALOGUE: tuple[Achievement, ...] = (
    # ---- Shipping ----
    Achievement(
        "forged_in_fire",
        "Shipped It",
        "shipping",
        "Pull requests embry0 opened that merged",
        "Somebody ring the bell.",
        "merged",
        (1, 5, 15, 30, 60, 120, 250),
        icon="anvil",
    ),
    Achievement(
        "no_death_run",
        "No-Death Run",
        "shipping",
        "Merged pull requests whose issue or pull request had no earlier attempt",
        "First try. No grave to run back to.",
        "first_try",
        _COUNTS,
        icon="sword",
    ),
    Achievement(
        "clean_kill",
        "Flawless Kill",
        "shipping",
        "Merged pull requests whose job never had an agent run end in an error",
        "Not a single scratch.",
        "clean_kill",
        _COUNTS,
        icon="sword",
    ),
    Achievement(
        "merged_for_a_penny",
        "Merged for a Penny",
        "shipping",
        "Merged pull requests whose job spent under one US dollar",
        "Cheapest upgrade in the game.",
        "bargain",
        (1, 3, 10, 25, 50, 100, 200),
        icon="coins",
    ),
    Achievement(
        "speedrun_any_percent",
        "Speedrun Any%",
        "shipping",
        "Pull requests merged within a day of their job starting",
        "Skipped every cutscene.",
        "speedrun",
        _COUNTS,
        icon="boots",
    ),
    Achievement(
        "repo_hopper",
        "Repo Hopper",
        "shipping",
        "Repositories with at least one merged pull request",
        "Same agent, new repository, same result.",
        "repo_hopper",
        _REPOS,
        icon="map",
    ),
    Achievement(
        "know_your_zones",
        "Know Your Zones",
        "shipping",
        "Merged pull requests that named the area of the monorepo they changed",
        "Never lost in the codebase again.",
        "area_merged",
        _COUNTS,
        icon="map",
    ),
    Achievement(
        "full_raid_party",
        "Full Raid Party",
        "shipping",
        "Merged pull requests whose job passed both a review and a QA run",
        "Tank, healer and damage all showed up.",
        "full_party",
        _COUNTS,
        icon="helm",
    ),
    Achievement(
        "hot_streak",
        "Hot Streak",
        "shipping",
        "Days with three merged pull requests or more",
        "The XP drops would not stop.",
        "hot_streak",
        (1, 3, 5, 10, 20, 40, 80),
        icon="flame",
    ),
    # ---- Hunting ----
    Achievement(
        "hunt_complete",
        "Hunt Complete",
        "hunting",
        "Production errors whose fix was checked against the logs afterwards and found healed",
        "Back to the board for the next one.",
        "healed",
        (1, 3, 10, 25, 50, 100, 200),
        icon="skull",
    ),
    Achievement(
        "out_of_reach",
        "Out of Reach",
        "hunting",
        "Production errors the watcher found itself that embry0's pull request then fixed",
        "Killed it before it could hit anyone.",
        "out_of_reach",
        (1, 3, 10, 25, 50, 100, 200),
        icon="shield",
    ),
    Achievement(
        "on_the_hunt",
        "On the Hunt",
        "hunting",
        "Watcher findings a person accepted for fixing",
        "This bug is somebody's job now.",
        "cases_accepted",
        _COUNTS,
        icon="scroll",
    ),
    Achievement(
        "boss_hunter",
        "Boss Hunter",
        "hunting",
        "High-severity production errors found healed",
        "It had a health bar the size of the screen.",
        "boss_healed",
        _RARE,
        icon="crown",
    ),
    Achievement(
        "elite_hunter",
        "Elite Hunter",
        "hunting",
        "Healed production errors that had been seen ten times or more",
        "It kept coming back. Not any more.",
        "superior_healed",
        _RARE,
        icon="skull",
    ),
    Achievement(
        "never_respawned",
        "Never Respawned",
        "hunting",
        "Healed production errors that never came back after their fix was checked",
        "It stayed down. They usually don't.",
        "never_respawned",
        (1, 3, 10, 25, 50, 100, 200),
        icon="grave",
    ),
    Achievement(
        "deployed_and_dusted",
        "Deployed and Dusted",
        "hunting",
        "Fixes for production errors that reached a deploy",
        "Loot picked up, teleported home.",
        "deployed_fix",
        _COUNTS,
        icon="coins",
    ),
    # ---- Questing ----
    Achievement(
        "quest_complete",
        "Quest Complete",
        "questing",
        "Issues finished by a merged pull request",
        "Done, and marked done.",
        "issue_done",
        (1, 5, 15, 30, 60, 120, 250),
        icon="scroll",
    ),
    Achievement(
        "side_quest",
        "Side Quest",
        "questing",
        "Sub-issues triage split off a bigger issue that a merged pull request finished",
        "You were only meant to deliver a letter.",
        "side_quest",
        _COUNTS,
        icon="scroll",
    ),
    Achievement(
        "talked_to_the_quest_giver",
        "Talked to the Quest Giver",
        "questing",
        "Answers owners gave to embry0's proposals: start it, not this, or later",
        "Yes, no, or later.",
        "owner_answer",
        (1, 5, 10, 25, 50, 100, 250),
        icon="speech",
    ),
    Achievement(
        "quest_accepted",
        "Quest Accepted",
        "questing",
        "Proposals an owner said yes to",
        "Yes, I will help you.",
        "proposal_accepted",
        _COUNTS,
        icon="quill",
    ),
    Achievement(
        "reward_claimed",
        "Reward Claimed",
        "questing",
        "Accepted proposals whose job's pull request merged",
        "The quest giver pays up.",
        "proposal_merged",
        _COUNTS,
        icon="lamp",
    ),
    Achievement(
        "answer_given",
        "Answer Given",
        "questing",
        "Questions an agent asked that a person answered",
        "The dialogue box finally moved on.",
        "question_answered",
        (1, 5, 10, 25, 50, 100, 250),
        icon="speech",
    ),
    # ---- Lore ----
    Achievement(
        "lines_straightened",
        "Lines Straightened",
        "lore",
        "Outdated claims the context audit found in the files that steer coding agents",
        "The tutor was lying the whole time.",
        "stale_claims",
        (10, 50, 100, 250, 500, 1_000, 2_500),
        icon="quill",
    ),
    Achievement(
        "lore_corrected",
        "Lore Corrected",
        "lore",
        "Context-audit pull requests that merged",
        "The library books are true again.",
        "audit_merged",
        _COUNTS,
        icon="book",
    ),
    Achievement(
        "weeded_garden",
        "Weeded Garden",
        "lore",
        "Context audits that found every instruction file already true",
        "Not a single weed in the patch.",
        "clean_audit",
        _COUNTS,
        icon="leaf",
    ),
    Achievement(
        "cartographer",
        "Cartographer",
        "lore",
        "Repositories whose areas map merged",
        "You can see the whole world map now.",
        "areas_mapped",
        _REPOS,
        icon="map",
    ),
    Achievement(
        "fashion_inspector",
        "Fashion Inspector",
        "lore",
        "Repositories whose design audit scored 90 or more against their standard",
        "Matching outfit, full set bonus.",
        "design_ninety",
        _REPOS,
        icon="gem",
    ),
    # ---- Guarding ----
    Achievement(
        "guardian_of_the_gate",
        "Guardian of the Gate",
        "guarding",
        "Reviews a reviewer agent finished",
        "None shall pass unread.",
        "reviews",
        (1, 10, 25, 50, 100, 250, 500),
        icon="shield",
    ),
    Achievement(
        "second_opinion",
        "Second Opinion",
        "guarding",
        "Pull request reviews embry0 posted on a pull request",
        "A second pair of eyes has left a comment.",
        "review_posted",
        (1, 10, 25, 50, 100, 250, 500),
        icon="eye",
    ),
    Achievement(
        "potion_master",
        "Potion Master",
        "guarding",
        "QA runs that completed",
        "Every dose checked before it was drunk.",
        "qa_passed",
        (1, 5, 15, 30, 60, 120, 250),
        icon="potion",
    ),
    Achievement(
        "you_shall_not_merge",
        "You Shall Not Merge",
        "guarding",
        "Changes the verification gate stopped because they broke tests that passed before",
        "Blocked just in time.",
        "gate_blocked",
        _RARE,
        icon="gate",
    ),
    Achievement(
        "training_grounds",
        "Training Grounds",
        "guarding",
        "Test setups embry0 proposed that a person applied to a repository",
        "Somewhere safe to practise.",
        "setup_applied",
        _REPOS,
        icon="target",
    ),
    # ---- Grit ----
    Achievement(
        "back_on_your_feet",
        "Back on Your Feet",
        "grit",
        "Merges on an issue whose earlier job failed",
        "Ran back from the gravestone and finished it.",
        "comeback",
        (1, 3, 5, 10, 25, 50, 100),
        secret=True,
        icon="grave",
    ),
    Achievement(
        "back_from_the_respawn",
        "Back from the Respawn",
        "grit",
        "Jobs a restart interrupted that resumed and finished their work",
        "Logged back in mid-fight and won.",
        "resumed",
        (1, 3, 5, 10, 25, 50, 100),
        icon="hourglass",
    ),
    Achievement(
        "lag_survivor",
        "Lag Survivor",
        "grit",
        "A job interrupted twice or more that still finished its work",
        "The server restarted. Twice. You lived.",
        "lag_survivor",
        (1,),
        secret=True,
        icon="hourglass",
    ),
    Achievement(
        "second_wind",
        "Second Wind",
        "grit",
        "Follow-up jobs on a pull request whose pull request then merged",
        "Caught a breath, kept going.",
        "followup_merged",
        _RARE,
        icon="heart",
    ),
    Achievement(
        "iron_will",
        "Iron Will",
        "grit",
        "Issues that merged on their third attempt or later",
        "Third time is the charm, apparently.",
        "iron_will",
        (1, 2, 3, 5, 10, 25, 50),
        icon="pickaxe",
    ),
    # ---- Growth ----
    Achievement(
        "elder_agent",
        "Elder Agent",
        "growth",
        "The highest total level of any one character",
        "Seen it all, levelled most of it.",
        "best_total",
        (100, 250, 500, 750, 1_000, 1_500, 2_000),
        icon="beard",
    ),
    Achievement(
        "mastery_cloak",
        "Mastery Cloak",
        "growth",
        "Skills at 99 across every character",
        "A cloak that says you did nothing else for weeks.",
        "capes",
        (1, 2, 5, 10, 23, 50, 100),
        icon="cape",
    ),
    Achievement(
        "fully_trained",
        "Fully Trained",
        "growth",
        "The highest level any one character reached",
        "One character, every stat that matters.",
        "best_level",
        (10, 25, 50, 75, 100, 115, 126),
        icon="sword",
    ),
    Achievement(
        "halfway_by_experience",
        "Halfway by Experience",
        "growth",
        "Skills that reached level 92, across every character",
        "Halfway there, by the experience table.",
        "level_92",
        (1, 2, 5, 10, 23, 50, 100),
        icon="star",
    ),
    Achievement(
        "thousand_club",
        "Thousand Club",
        "growth",
        "Characters whose total level reached 1,000",
        "The roster stopped being a bunch of noobs.",
        "thousand_club",
        (1, 2, 3, 5, 8, 12, 18),
        icon="star",
    ),
    Achievement(
        "rare_finds",
        "Rare Finds",
        "growth",
        "Rare drops characters earned",
        "Worth framing.",
        "rares",
        _REPOS,
        icon="gem",
    ),
    # ---- Oddities ----
    Achievement(
        "afk_weekend",
        "AFK Weekend",
        "oddities",
        "A pull request that merged on a Saturday or Sunday (UTC)",
        "The agents grind while you touch grass.",
        "weekend_merge",
        (1,),
        secret=True,
        icon="moon",
    ),
    Achievement(
        "night_shift",
        "Night Shift",
        "oddities",
        "A pull request that merged between midnight and four in the morning (UTC)",
        "Nobody else was online.",
        "midnight_merge",
        (1,),
        secret=True,
        icon="moon",
    ),
    Achievement(
        "friday_night_merge",
        "Friday Night Merge",
        "oddities",
        "A pull request that merged on a Friday after eight in the evening (UTC)",
        "Bold. Very bold.",
        "friday_night",
        (1,),
        secret=True,
        icon="flame",
    ),
    Achievement(
        "holiday_event",
        "Holiday Event",
        "oddities",
        "A pull request that merged on 25 December, 31 October or 1 January (UTC)",
        "Limited-time event. Log in to claim.",
        "holiday_merge",
        (1,),
        secret=True,
        icon="gift",
    ),
    Achievement(
        "long_grind",
        "Long Grind",
        "oddities",
        "A pull request that merged from a job that ran for more than two hours",
        "This was supposed to be a quick fix.",
        "long_grind",
        (1,),
        secret=True,
        icon="hourglass",
    ),
    Achievement(
        "lucky_find",
        "Lucky Find",
        "oddities",
        "A pull request that merged from the first job embry0 ever ran on its repository",
        "Beginner's luck.",
        "lucky_find",
        (1,),
        secret=True,
        icon="paw",
    ),
    Achievement(
        "ninety_nine_problems",
        "99 Problems",
        "oddities",
        "A production error seen 99 times or more that a merged fix killed",
        "But a bug ain't one.",
        "ninety_nine",
        (1,),
        secret=True,
        icon="skull",
    ),
)

#: A metric's value before any step: a character starts with every skill at 1, and at level 1.
_BASE: dict[str, float] = {"best_total": float(len(STATS)), "best_level": 1.0}

#: The total level a character reaches to join the Thousand Club.
THOUSAND = 1_000
#: Level 92 holds half the experience of level 99.
HALF_OF_MAX = 92


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
) -> dict[str, list[Step]]:
    """Every metric as the steps it rose by, each with its time and evidence, oldest first."""
    steps: dict[str, list[Step]] = defaultdict(list)
    for fact in facts:
        if fact.metric == "audit":
            # A completed context audit: its outdated claims, or, with none, a clean audit.
            stale, files = report_counts(fact.report)
            if stale:
                steps["stale_claims"].append(Step(fact.at, stale, fact.label, fact.url))
            elif files:
                steps["clean_audit"].append(Step(fact.at, 1, fact.label, fact.url))
            continue
        steps[fact.metric].append(Step(fact.at, fact.value, fact.label, fact.url))
    # The highest total level of any character, stepping as its skills level; the capes; the Thousand Club.
    levels: dict[tuple[str, str], int] = {}
    totals: dict[str, int] = defaultdict(lambda: len(STATS))
    best = len(STATS)
    reached_92: set[tuple[str, str]] = set()
    for up in sorted(level_ups, key=lambda u: u.at):
        key = (up.agent_type, up.stat)
        before = totals[up.agent_type]
        totals[up.agent_type] += up.level - levels.get(key, 1)
        levels[key] = up.level
        if totals[up.agent_type] > best:
            steps["best_total"].append(Step(up.at, totals[up.agent_type] - best, up.agent_type))
            best = totals[up.agent_type]
        if up.level == MAX_LEVEL:
            steps["capes"].append(Step(up.at, 1, f"{up.agent_type} · {up.stat}"))
        if up.level >= HALF_OF_MAX and key not in reached_92:
            reached_92.add(key)
            steps["level_92"].append(Step(up.at, 1, f"{up.agent_type} · {up.stat}"))
        if before < THOUSAND <= totals[up.agent_type]:
            steps["thousand_club"].append(Step(up.at, 1, up.agent_type))
    best_level = int(_BASE["best_level"])
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
    achievement: Achievement
    value: float
    #: When each reached tier was crossed, Bronze first (an objective has at most one).
    tier_at: list[datetime] = field(default_factory=list)
    #: The step that last moved the metric.
    evidence: Step | None = None

    @property
    def tier(self) -> int:
        """Tiers reached: 0 for none, 7 for Divine; 1 for a complete objective."""
        return len(self.tier_at)

    @property
    def hidden(self) -> bool:
        return self.achievement.secret and self.value <= _BASE.get(self.achievement.metric, 0.0)

    @property
    def tier_name(self) -> str | None:
        if not self.tier:
            return None
        return COMPLETE if self.achievement.objective else TIER_NAMES[self.tier - 1]

    def to_dict(self) -> dict[str, Any]:
        a = self.achievement
        if self.hidden:
            return {
                "id": a.id,
                "name": "???",
                "category": a.category,
                "kind": "objective" if a.objective else "tiered",
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
        return {
            "id": a.id,
            "name": a.name,
            "category": a.category,
            "kind": "objective" if a.objective else "tiered",
            "icon": a.icon,
            "what_counts": a.what_counts,
            "flavour": a.flavour,
            "secret": a.secret,
            "state": "unlocked" if self.tier else "discovered",
            "tier": self.tier_name,
            "tiers_reached": self.tier,
            "value": int(self.value),
            "next_threshold": None if self.tier >= len(a.thresholds) else a.thresholds[self.tier],
            "thresholds": list(a.thresholds),
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
) -> tuple[list[AchievementState], list[Unlock]]:
    """Each achievement's value, tiers and evidence, and every tier unlock in time order."""
    steps = metric_steps(facts, level_ups, agent_level_ups, drops)
    states: list[AchievementState] = []
    unlocks: list[Unlock] = []
    for achievement in CATALOGUE:
        state = AchievementState(achievement, _BASE.get(achievement.metric, 0.0))
        thresholds = achievement.thresholds
        for step in steps.get(achievement.metric, []):
            state.value += step.value
            state.evidence = step
            while state.tier < len(thresholds) and state.value >= thresholds[state.tier]:
                state.tier_at.append(step.at)
                unlocks.append(Unlock(step.at, achievement.id, achievement.name, state.tier_name or COMPLETE))
        states.append(state)
    unlocks.sort(key=lambda u: u.at)
    return states, unlocks
