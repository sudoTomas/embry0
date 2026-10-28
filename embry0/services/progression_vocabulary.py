"""What a progression content pack can name (#176): the vocabulary the code defines.

A pack fills this vocabulary; it never extends it. Metrics, quest kinds and
diary task kinds are logic the code implements, each with the parameters a
pack sets; action kinds and categories are what the classifier emits;
outcome sources are what the progression repository reads; the art ids are
what the frontend can draw (``docs/contracts/progression.json`` pins them on
both sides).

A leaf module: it imports pydantic, the standard library and the classifier's
vocabulary only, so the pack schema, the services and the repository all
import it without a cycle.
"""

from __future__ import annotations

import string
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from embry0.execution.skill_actions import ACTION_CATEGORIES, ACTION_KEYS

__all__ = [
    "ACHIEVEMENT_ICONS",
    "ACTION_CATEGORIES",
    "ACTION_KEYS",
    "ACTION_KINDS",
    "GEAR_STEPS",
    "LABEL_PLACEHOLDERS",
    "MESSAGE_PLACEHOLDERS",
    "METRICS",
    "OUTCOME_SOURCES",
    "QUEST_KINDS",
    "RARE_ART",
    "TASK_KINDS",
    "AgentName",
    "Metric",
    "Params",
    "QuestKind",
    "placeholders",
]

#: The kinds of tool call, each priced by the pack's ``rates.actions``.
ACTION_KINDS: tuple[str, ...] = tuple(ACTION_CATEGORIES)

#: The outcomes the progression repository reads, each priced by the pack's ``rates.outcomes``.
OUTCOME_SOURCES: tuple[str, ...] = (
    "merged",
    "opened",
    "qa_passed",
    "audited",
    "asked",
    "comeback",
    "resumed",
    "cleaned",
)

# ---- art: what the frontend draws (frontend/src/components/roster/appearance, achievementIconData.ts) ----

#: The gear ladders' steps, lowest first. A pack's tier names one of them.
GEAR_STEPS: tuple[str, ...] = ("bronze", "silver", "gold", "platinum", "diamond", "obsidian", "divine")
#: The head items a rare can wear.
RARE_ART: tuple[str, ...] = ("golden-paper-crown", "paper-crown", "harvest-mask")
#: The achievement icons (the ``secret`` glyph is the page's own, for an entry still hidden).
ACHIEVEMENT_ICONS: tuple[str, ...] = (
    "anvil",
    "sword",
    "coins",
    "boots",
    "map",
    "flame",
    "helm",
    "skull",
    "shield",
    "scroll",
    "crown",
    "grave",
    "speech",
    "quill",
    "book",
    "leaf",
    "gem",
    "potion",
    "gate",
    "target",
    "hourglass",
    "heart",
    "pickaxe",
    "beard",
    "cape",
    "star",
    "moon",
    "gift",
    "eye",
    "lamp",
    "paw",
)

# ---- words ----

#: The placeholders each chat-box message may use.
MESSAGE_PLACEHOLDERS: dict[str, frozenset[str]] = {
    "level": frozenset({"character", "skill", "level"}),
    "agent_level": frozenset({"character", "level"}),
    "rare": frozenset({"character", "rare"}),
    "quest": frozenset({"name"}),
    "diary": frozenset({"tier", "repo"}),
    "achievement_tier": frozenset({"name", "tier"}),
    "achievement_complete": frozenset({"name"}),
}

#: The nouns the progression components print, with the placeholders each may use.
LABEL_PLACEHOLDERS: dict[str, frozenset[str]] = {
    "complete": frozenset(),
    "quest_journal": frozenset(),
    "journal_points": frozenset(),
    "quest_point": frozenset(),
    "quest_points": frozenset(),
    "gear": frozenset({"tier"}),
    "mastery_cloak": frozenset({"skill"}),
    "trimmed_cloak": frozenset({"skill"}),
    "quest_cloak": frozenset(),
}


def placeholders(text: str) -> set[str]:
    """The ``{name}`` fields of a format string; raises ValueError when it cannot be parsed."""
    names: set[str] = set()
    for _literal, field, _spec, _conversion in string.Formatter().parse(text):
        if field is None:
            continue
        if not field.isidentifier():
            raise ValueError(f"{{{field}}} is not a placeholder name")
        names.add(field)
    return names


# ---- parameters ----

AgentName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$", max_length=64)]
MonthDay = Annotated[str, StringConstraints(pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")]
Agents = Annotated[list[AgentName], Field(min_length=1, max_length=20)]


class Params(BaseModel):
    """The parameters one metric, quest kind or task kind takes. A pack's values are checked against it."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class NoParams(Params):
    pass


class MaxUsd(Params):
    max_usd: float = Field(gt=0, le=1_000_000, description="the job's spend must be under this many US dollars")


class Hours(Params):
    hours: float = Field(gt=0, le=10_000)


class Count(Params):
    count: int = Field(ge=1, le=1_000_000)


class ReviewAndQa(Params):
    review_agents: Agents
    qa_agents: Agents


class Healed(Params):
    severity: str | None = Field(default=None, max_length=40, description="only cases of this severity")
    min_seen: int | None = Field(default=None, ge=1, description="only cases seen at least this many times")


class MinScore(Params):
    min_score: int = Field(ge=0, le=100)


class AgentsOnly(Params):
    agents: Agents


class MinResumes(Params):
    min_resumes: int = Field(ge=1, le=1_000)


class MinAttempts(Params):
    min_attempts: int = Field(ge=1, le=1_000)


class LocalTime(Params):
    weekdays: list[int] = Field(min_length=1, max_length=7, description="ISO weekdays, 1 Monday to 7 Sunday")
    from_hour: int = Field(ge=0, le=23)
    to_hour: int = Field(ge=1, le=24)

    @model_validator(mode="after")
    def _hours_and_days(self) -> LocalTime:
        if any(day < 1 or day > 7 for day in self.weekdays):
            raise ValueError("weekdays are 1 (Monday) to 7 (Sunday)")
        if self.from_hour >= self.to_hour:
            raise ValueError("from_hour must be before to_hour")
        return self


class Dates(Params):
    dates: list[MonthDay] = Field(min_length=1, max_length=366, description="MM-DD, in the pack's timezone")


class MinSeen(Params):
    min_seen: int = Field(ge=1, le=1_000_000)


class Level(Params):
    level: int = Field(ge=1, le=200)


class Total(Params):
    total: int = Field(ge=1, le=100_000)


class CountAgents(Params):
    count: int = Field(ge=1, le=1_000_000)
    agents: Agents


class Days(Params):
    days: int = Field(ge=1, le=3_650)


class Capstone(Params):
    merges: int = Field(ge=1, le=1_000_000)
    diary_tier: str = Field(min_length=1, max_length=40, description="the id of the diary tier a repository completes")
    total_level: int = Field(ge=1, le=100_000)


# ---- metrics ----


@dataclass(frozen=True)
class Metric:
    """An achievement metric: its parameters, and where its steps come from.

    ``fragment`` names the SQL fragment of the facts query
    (``embry0/storage/repositories/progression.py``); a metric without one is
    derived from the level-ups in Python.
    """

    params: type[Params]
    fragment: str | None


def _sql(params: type[Params] = NoParams, fragment: str = "") -> Metric:
    return Metric(params, fragment)


#: Every metric, by id. Ids are stable: an achievement names one.
METRICS: dict[str, Metric] = {
    # shipping
    "merged": _sql(fragment="merged"),
    "first_try": _sql(fragment="first_try"),
    "clean_kill": _sql(fragment="clean_kill"),
    "merged_under_cost": _sql(MaxUsd, "merged_under_cost"),
    "merged_within": _sql(Hours, "merged_within"),
    "repo_hopper": _sql(fragment="repo_hopper"),
    "area_merged": _sql(fragment="area_merged"),
    "merges_in_a_day": _sql(Count, "merges_in_a_day"),
    "merged_reviewed_and_tested": _sql(ReviewAndQa, "merged_reviewed_and_tested"),
    # hunting
    "healed": _sql(Healed, "healed"),
    "never_respawned": _sql(fragment="never_respawned"),
    "out_of_reach": _sql(fragment="out_of_reach"),
    "cases_accepted": _sql(fragment="cases_accepted"),
    "deployed_fix": _sql(fragment="deployed_fix"),
    "fixed_recurring": _sql(MinSeen, "fixed_recurring"),
    # questing
    "issue_done": _sql(fragment="issue_done"),
    "side_quest": _sql(fragment="side_quest"),
    "owner_answer": _sql(fragment="owner_answer"),
    "proposal_accepted": _sql(fragment="proposal_accepted"),
    "proposal_merged": _sql(fragment="proposal_merged"),
    "question_answered": _sql(fragment="question_answered"),
    # lore: a completed context audit is one fact, read as its outdated claims or as a clean audit
    "stale_claims": _sql(fragment="audit"),
    "clean_audit": _sql(fragment="audit"),
    "audit_merged": _sql(fragment="audit_merged"),
    "areas_mapped": _sql(fragment="areas_mapped"),
    "design_score": _sql(MinScore, "design_score"),
    # guarding
    "reviews": _sql(AgentsOnly, "reviews"),
    "review_posted": _sql(fragment="review_posted"),
    "qa_passed": _sql(fragment="qa_passed"),
    "gate_blocked": _sql(fragment="gate_blocked"),
    "setup_applied": _sql(fragment="setup_applied"),
    # grit
    "comeback": _sql(fragment="comeback"),
    "resumed": _sql(MinResumes, "resumed"),
    "followup_merged": _sql(fragment="followup_merged"),
    "merged_on_attempt": _sql(MinAttempts, "merged_on_attempt"),
    # oddities
    "merged_at_local_time": _sql(LocalTime, "merged_at_local_time"),
    "merged_on_dates": _sql(Dates, "merged_on_dates"),
    "merged_running_over": _sql(Hours, "merged_running_over"),
    "lucky_find": _sql(fragment="lucky_find"),
    # growth: from the level-ups
    "best_total": Metric(NoParams, None),
    "capes": Metric(NoParams, None),
    "best_level": Metric(NoParams, None),
    "rares": Metric(NoParams, None),
    "skills_at_level": Metric(Level, None),
    "characters_at_total": Metric(Total, None),
}


# ---- quests and diary tasks ----


@dataclass(frozen=True)
class QuestKind:
    """A quest's logic (``embry0/services/quests.py``): its step count and parameters.

    ``capstone`` alone has a total-level requirement and may trim every
    cloak, and it gives no XP: its requirement is read from the levels, which
    quest XP feeds.
    """

    steps: int
    params: type[Params]
    capstone: bool = False


QUEST_KINDS: dict[str, QuestKind] = {
    "onboard_to_merge": QuestKind(3, NoParams),
    "merge_after_failure": QuestKind(2, NoParams),
    "review_qa_merge": QuestKind(3, ReviewAndQa),
    "capstone": QuestKind(2, Capstone, capstone=True),
}

#: A diary task's logic (``embry0/services/quests.py``), by kind: its parameters.
TASK_KINDS: dict[str, type[Params]] = {
    "triage": AgentsOnly,
    "finish_job": NoParams,
    "run_qa": AgentsOnly,
    "merges": Count,
    "pass_qa": AgentsOnly,
    "audit": NoParams,
    "clean_audit": NoParams,
    "reviews": CountAgents,
    "quiet_stretch": Days,
    "all_above": NoParams,
}


def dump_params(model: Params) -> dict[str, Any]:
    """A validated parameter model as plain values, the defaults it was not given left out."""
    return model.model_dump(mode="json", exclude_defaults=True)
