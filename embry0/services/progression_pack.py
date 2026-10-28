"""The progression content pack (#176): its schema, its loader and validator, and the rules priced from it.

Everything the progression layer shows is the active pack's: the skills and
the action categories each one is trained by, the experience curve and
rates, the tiers and the gear art each one wears, the rares, the
achievements (a metric and its parameters each), the quests and diary
tasks (a kind and its parameters each), and the words of the chat box. The
schema lives here and the vocabulary it may name in
``progression_vocabulary``; the values are a document. embry0 ships one,
``progression_pack_default.yaml``, the neutral default every deployment
reads until an operator activates another.

A document is checked in two passes: its shape (pydantic, every field
strict, no unknown key) and then its cross-references (a skill's group, a
metric's parameters, one threshold per tier...). Every problem is collected
and returned with its location, as the settings document reports them (#160),
so an API answers 422 naming every bad key at once.

:func:`rules_for` turns a valid pack into :class:`Rules`, the lookups the
services price with. :class:`ShippedPack` is the pack source that always
answers the shipped default; a stored source replaces it when operators can
change the pack.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from embry0.services import progression_templates as templates
from embry0.services import progression_vocabulary as vocab
from embry0.text_documents import structure_problems

#: The schema version this release reads and writes.
SCHEMA_VERSION = 1
#: The largest document the loader parses.
MAX_TEXT_BYTES = 256 * 1024
#: The deepest a document nests; the shipped pack nests six deep.
MAX_DEPTH = 32
#: The most experience the curve's top may hold: past it, the dashboard cannot read a number exactly.
MAX_CURVE_XP = 2**53 - 1
#: The shipped default, beside this module.
DEFAULT_PACK_FILE = "progression_pack_default.yaml"
#: The cloak a skill wears at 99 when the pack gives it no colours.
NEUTRAL_CLOAK = {"base": "#55525e", "trim": "#9a9aa2"}

Id = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")]
Name = Annotated[str, StringConstraints(min_length=1, max_length=80)]
Text = Annotated[str, StringConstraints(min_length=1, max_length=300)]
Hex = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
Xp = Annotated[int, Field(ge=0, le=10_000_000)]
#: Names the vocabulary defines (``progression_vocabulary``); the cross-check pass refuses any other.
Word = Annotated[str, StringConstraints(min_length=1, max_length=40)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Curve(_Model):
    """Level L starts at ``floor(sum_{n<L} floor(n + points * 2^(n / doubling)) / divisor)``."""

    max_level: int = Field(ge=2, le=200)
    points: float = Field(gt=0, le=100_000)
    doubling: float = Field(ge=1, le=1_000)
    divisor: float = Field(ge=1, le=1_000)


class Group(_Model):
    id: Id
    name: Name


class Cloak(_Model):
    base: Hex
    trim: Hex


class Skill(_Model):
    id: Id
    name: Name
    group: Id
    trained_by: list[Word] = Field(default_factory=list, max_length=len(vocab.ACTION_KEYS))
    cloak: Cloak | None = None


class Outcome(_Model):
    skill: Id
    xp: Xp


class AuditedOutcome(Outcome):
    per_stale_claim: Xp = 0


class CleanedOutcome(Outcome):
    per_line_removed: Xp = 0
    max_lines: int = Field(default=0, ge=0, le=1_000_000)


class Outcomes(_Model):
    merged: Outcome | None = None
    opened: Outcome | None = None
    qa_passed: Outcome | None = None
    audited: AuditedOutcome | None = None
    asked: Outcome | None = None
    comeback: Outcome | None = None
    resumed: Outcome | None = None
    cleaned: CleanedOutcome | None = None


class RunOutcome(_Model):
    agent: vocab.AgentName
    skill: Id
    xp: Xp


class Rates(_Model):
    actions: dict[Word, Xp] = Field(default_factory=dict)
    outcomes: Outcomes = Field(default_factory=Outcomes)
    runs: list[RunOutcome] = Field(default_factory=list, max_length=100)


class Tier(_Model):
    id: Id
    name: Name
    from_level: int = Field(ge=1, le=200)
    color: Hex
    gear: Word


class Cloaks(_Model):
    quest: Cloak


class RareWhen(_Model):
    merge_on: vocab.MonthDay | None = None
    first_run_of_year: bool = False


class Rare(_Model):
    id: Id
    name: Name
    art: Word | None = None
    when: RareWhen


class AchievementCategory(_Model):
    id: Id
    name: Name
    blurb: Text


class Achievement(_Model):
    id: Id
    name: Name
    category: Id
    what_counts: Text
    flavour: Text
    icon: Word
    secret: bool = False
    metric: Word
    params: dict[str, Any] = Field(default_factory=dict)
    thresholds: list[int] = Field(min_length=1, max_length=12)


class QuestXp(_Model):
    agent: vocab.AgentName
    skill: Id
    xp: Xp


class Quest(_Model):
    id: Id
    kind: Word
    name: Name
    points: int = Field(ge=0, le=1_000)
    reward: Text
    steps: list[Text] = Field(min_length=1, max_length=10)
    requirement: Text | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    xp: list[QuestXp] = Field(default_factory=list, max_length=20)
    cloak_trim: bool = False


class Task(_Model):
    kind: Word
    params: dict[str, Any] = Field(default_factory=dict)
    text: Text


class DiaryTier(_Model):
    id: Id
    name: Name
    tasks: list[Task] = Field(min_length=1, max_length=10)


class Diaries(_Model):
    tiers: list[DiaryTier] = Field(min_length=1, max_length=8)


class Messages(_Model):
    level: Text
    agent_level: Text
    rare: Text
    quest: Text
    diary: Text
    achievement_tier: Text
    achievement_complete: Text


class Labels(_Model):
    complete: Name
    quest_journal: Name
    journal_points: Name
    quest_point: Name
    quest_points: Name
    gear: Name
    mastery_cloak: Name
    trimmed_cloak: Name
    quest_cloak: Name


class Pack(_Model):
    """One content pack: everything the progression layer names, counts and says."""

    version: int = Field(default=SCHEMA_VERSION, description="the schema version the document is written in")
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=64)] = "UTC"
    curve: Curve
    groups: list[Group] = Field(min_length=1, max_length=8)
    skills: list[Skill] = Field(min_length=1, max_length=64)
    rates: Rates
    tiers: list[Tier] = Field(min_length=1, max_length=12)
    cloaks: Cloaks
    rares: list[Rare] = Field(default_factory=list, max_length=24)
    achievement_categories: list[AchievementCategory] = Field(min_length=1, max_length=24)
    achievements: list[Achievement] = Field(default_factory=list, max_length=200)
    quests: list[Quest] = Field(default_factory=list, max_length=32)
    diaries: Diaries
    messages: Messages
    labels: Labels


# ---- problems ----


@dataclass(frozen=True)
class Problem:
    """One reason a document cannot be the pack: where it is, what kind, and what is wrong.

    The shape of FastAPI's validation errors (and of the settings document's
    422), so the dashboard renders it as it renders any other.
    """

    loc: tuple[str | int, ...]
    type: str
    msg: str

    @property
    def key(self) -> str:
        """The location as a dotted key: ``achievements[3].thresholds``."""
        out = ""
        for part in self.loc:
            out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
        return out

    def to_dict(self) -> dict[str, Any]:
        return {"loc": list(self.loc), "type": self.type, "msg": self.msg}

    def __str__(self) -> str:
        return f"{self.key}: {self.msg}" if self.loc else self.msg


class PackInvalid(ValueError):
    """The document is not a valid pack; ``problems`` says why, every one of them."""

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        super().__init__("; ".join(str(p) for p in problems[:5]))


def _from_pydantic(error: ValidationError, prefix: tuple[str | int, ...] = ()) -> list[Problem]:
    return [Problem((*prefix, *e["loc"]), e["type"], e["msg"]) for e in error.errors(include_input=False)]


# ---- loading ----


class _NoAliasLoader(yaml.SafeLoader):
    """A safe loader that refuses aliases: a small text could expand into a large document."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            mark = self.peek_event().start_mark  # type: ignore[no-untyped-call]  # the stubs leave it untyped
            raise yaml.YAMLError(f"line {mark.line + 1}, column {mark.column + 1}: aliases are not allowed")
        return super().compose_node(parent, index)


def _too_deep(data: Any) -> bool:
    """Whether ``data`` nests deeper than :data:`MAX_DEPTH`, found without recursion."""
    stack: list[tuple[Any, int]] = [(data, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            return True
        if isinstance(node, dict):
            stack.extend((value, depth + 1) for value in node.values())
        elif isinstance(node, list):
            stack.extend((value, depth + 1) for value in node)
    return False


def _yaml_problem(exc: yaml.YAMLError) -> Problem:
    mark = getattr(exc, "problem_mark", None)
    where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark is not None else ""
    problem = getattr(exc, "problem", None) or str(exc)
    return Problem((), "yaml_syntax", f"{where}{problem}")


def load_text(text: str) -> Any:
    """The document a YAML or JSON text holds; raises :class:`PackInvalid` when it cannot be read.

    Besides YAML's own errors it refuses what every operator-written
    document refuses (:mod:`embry0.text_documents`): an alias, a merge key,
    a key that is not text and a key written twice, which YAML would keep
    silently. So the shipped default, ``--check``, ``embry0
    progression-pack check`` and every write meet the same rules.
    """
    if len(text.encode()) > MAX_TEXT_BYTES:
        raise PackInvalid([Problem((), "too_large", f"the document is larger than {MAX_TEXT_BYTES // 1024} KiB")])
    too_deep = Problem((), "too_deep", f"the document nests deeper than {MAX_DEPTH} levels")
    try:
        root = yaml.compose(text, Loader=_NoAliasLoader)  # noqa: S506 - a SafeLoader subclass
        structure = structure_problems(root, what="a content pack")
        if structure:
            raise PackInvalid([Problem(p.loc, p.type, p.msg) for p in structure])
        data = yaml.load(text, Loader=_NoAliasLoader)  # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError as exc:
        raise PackInvalid([_yaml_problem(exc)]) from None
    except RecursionError:
        raise PackInvalid([too_deep]) from None
    if _too_deep(data):
        raise PackInvalid([too_deep])
    return data


def parse(data: Any) -> Pack:
    """The pack ``data`` is, checked in full; raises :class:`PackInvalid` naming every problem."""
    if not isinstance(data, dict):
        raise PackInvalid([Problem((), "dict_type", "a pack is a mapping of sections")])
    if _too_deep(data):
        raise PackInvalid([Problem((), "too_deep", f"the document nests deeper than {MAX_DEPTH} levels")])
    if "version" in data and data["version"] != SCHEMA_VERSION:
        raise PackInvalid(
            [
                Problem(
                    ("version",),
                    "version",
                    f"this release reads version {SCHEMA_VERSION}; the shipped pack is at GET /progression/pack/default",
                )
            ]
        )
    try:
        pack = Pack.model_validate(data)
    except ValidationError as exc:
        raise PackInvalid(_from_pydantic(exc)) from None
    problems = cross_check(pack)
    if problems:
        raise PackInvalid(problems)
    return pack


def parse_text(text: str) -> Pack:
    return parse(load_text(text))


# ---- cross-references ----


def _duplicates(items: list[Any], section: str) -> list[Problem]:
    seen: set[str] = set()
    out: list[Problem] = []
    for i, item in enumerate(items):
        if item.id in seen:
            out.append(Problem((section, i, "id"), "duplicate", f"this id is already used in {section}"))
        seen.add(item.id)
    return out


def _check_params(model: type[vocab.Params], params: dict[str, Any], loc: tuple[str | int, ...]) -> list[Problem]:
    try:
        model.model_validate(params)
    except ValidationError as exc:
        return _from_pydantic(exc, loc)
    return []


def _check_text(
    text: str, allowed: set[str] | frozenset[str], loc: tuple[str | int, ...], values: dict[str, Any] | None = None
) -> list[Problem]:
    """A text in the template grammar (``progression_templates``) that names only ``allowed`` fields.

    ``values``, when given, are the parameters the text is filled from: a
    list or a mapping cannot be written into a text.
    """
    try:
        names = templates.fields(text)
    except templates.TemplateError as exc:
        return [Problem(loc, "template", f"not a template: {exc}")]
    known = ", ".join(f"{{{n}}}" for n in sorted(allowed)) or "none"
    if names - set(allowed):
        return [Problem(loc, "placeholder", f"names a value this text does not have; it may use {known}")]
    if values is not None and any(isinstance(values.get(n), list | dict) for n in names):
        return [Problem(loc, "placeholder", "a list cannot be written into a text")]
    return []


def _xp_table(curve: Curve) -> tuple[int, ...]:
    """The experience each level starts at, 1 to ``max_level``; raises OverflowError past what a float holds."""
    table, points = [0], 0
    for n in range(1, curve.max_level):
        points += math.floor(n + curve.points * 2 ** (n / curve.doubling))
        table.append(math.floor(points / curve.divisor))
    return tuple(table)


def cross_check(pack: Pack) -> list[Problem]:
    """Every cross-reference problem of a pack whose shape is valid."""
    problems: list[Problem] = []
    try:
        ZoneInfo(pack.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        problems.append(Problem(("timezone",), "timezone", "not a timezone name (IANA, such as UTC)"))
    try:
        table = _xp_table(pack.curve)
    except OverflowError:
        table = (0, MAX_CURVE_XP + 1)
    if any(b <= a for a, b in zip(table, table[1:], strict=False)):
        problems.append(
            Problem(("curve",), "curve", "the curve must give every level more experience than the one below")
        )
    if table[-1] > MAX_CURVE_XP:
        problems.append(
            Problem(
                ("curve",),
                "curve",
                f"the curve's top must stay under {MAX_CURVE_XP:,} XP, which the dashboard reads exactly",
            )
        )

    for section in ("groups", "skills", "tiers", "rares", "achievement_categories", "achievements", "quests"):
        problems += _duplicates(getattr(pack, section), section)
    problems += _duplicates(pack.diaries.tiers, "diaries.tiers")

    def known(value: str, names: Any, what: str, loc: tuple[str | int, ...]) -> bool:
        if value in names:
            return True
        listed = ", ".join(sorted(names))
        problems.append(Problem(loc, "unknown", f"not {what}; one of: {listed}"))
        return False

    for kind in pack.rates.actions:
        known(kind, vocab.ACTION_KINDS, "an action kind", ("rates", "actions", kind))
    for i, tier in enumerate(pack.tiers):
        known(tier.gear, vocab.GEAR_STEPS, "a gear step the dashboard draws", ("tiers", i, "gear"))
    for i, rare in enumerate(pack.rares):
        if rare.art is not None:
            known(rare.art, vocab.RARE_ART, "a rare the dashboard draws", ("rares", i, "art"))

    groups = {g.id for g in pack.groups}
    skills = {s.id for s in pack.skills}
    trained: dict[str, str] = {}
    for i, skill in enumerate(pack.skills):
        if skill.group not in groups:
            problems.append(Problem(("skills", i, "group"), "unknown", "no group has this id"))
        for j, key in enumerate(skill.trained_by):
            if not known(key, vocab.ACTION_KEYS, "an action category", ("skills", i, "trained_by", j)):
                continue
            if key in trained:
                problems.append(
                    Problem(
                        ("skills", i, "trained_by", j),
                        "duplicate",
                        f"this category already trains skills[{[s.id for s in pack.skills].index(trained[key])}]; a category trains one skill",
                    )
                )
            trained.setdefault(key, skill.id)

    def need_skill(skill: str, loc: tuple[str | int, ...]) -> None:
        if skill not in skills:
            problems.append(Problem(loc, "unknown", "no skill has this id"))

    for source in vocab.OUTCOME_SOURCES:
        outcome = getattr(pack.rates.outcomes, source)
        if outcome is not None:
            need_skill(outcome.skill, ("rates", "outcomes", source, "skill"))
    agents_seen: set[str] = set()
    for i, run in enumerate(pack.rates.runs):
        need_skill(run.skill, ("rates", "runs", i, "skill"))
        if run.agent in agents_seen:
            problems.append(Problem(("rates", "runs", i, "agent"), "duplicate", "this agent already has a run rate"))
        agents_seen.add(run.agent)

    for i, tier in enumerate(pack.tiers):
        if i == 0 and tier.from_level != 1:
            problems.append(Problem(("tiers", 0, "from_level"), "first_tier", "the first tier starts at level 1"))
        if i and tier.from_level <= pack.tiers[i - 1].from_level:
            problems.append(
                Problem(("tiers", i, "from_level"), "order", "each tier starts at a higher level than the one before")
            )
        if tier.from_level > pack.curve.max_level:
            problems.append(
                Problem(("tiers", i, "from_level"), "range", f"above the maximum level {pack.curve.max_level}")
            )

    dates: set[str] = set()
    yearly = 0
    for i, rare in enumerate(pack.rares):
        when = rare.when
        if (when.merge_on is None) == (not when.first_run_of_year):
            problems.append(Problem(("rares", i, "when"), "when", "name exactly one of merge_on and first_run_of_year"))
        if when.merge_on is not None:
            if when.merge_on in dates:
                problems.append(
                    Problem(("rares", i, "when", "merge_on"), "duplicate", "another rare already drops on this date")
                )
            dates.add(when.merge_on)
        yearly += when.first_run_of_year
        if when.first_run_of_year and yearly > 1:
            problems.append(Problem(("rares", i, "when"), "duplicate", "another rare drops on the first run of a year"))

    categories = {c.id for c in pack.achievement_categories}
    tiers = len(pack.tiers)
    for i, achievement in enumerate(pack.achievements):
        loc: tuple[str | int, ...] = ("achievements", i)
        if achievement.category not in categories:
            problems.append(Problem((*loc, "category"), "unknown", "no achievement category has this id"))
        known(achievement.icon, vocab.ACHIEVEMENT_ICONS, "an icon the dashboard draws", (*loc, "icon"))
        if not known(achievement.metric, vocab.METRICS, "a metric", (*loc, "metric")):
            continue
        metric = vocab.METRICS[achievement.metric]
        param_problems = _check_params(metric.params, achievement.params, (*loc, "params"))
        problems += param_problems
        thresholds = achievement.thresholds
        if len(thresholds) not in (1, tiers):
            problems.append(
                Problem(
                    (*loc, "thresholds"),
                    "thresholds",
                    f"{len(thresholds)} values; an objective has one, a tiered achievement one per tier ({tiers})",
                )
            )
        if any(b <= a for a, b in zip(thresholds, thresholds[1:], strict=False)) or thresholds[0] < 1:
            problems.append(Problem((*loc, "thresholds"), "order", "thresholds start at 1 or more and rise"))
        problems += _check_text(
            achievement.what_counts, set(achievement.params), (*loc, "what_counts"), achievement.params
        )
        if achievement.metric == "skills_at_level" and not param_problems:
            if achievement.params["level"] > pack.curve.max_level:
                problems.append(
                    Problem((*loc, "params", "level"), "range", f"above the maximum level {pack.curve.max_level}")
                )

    diary_tiers = [t.id for t in pack.diaries.tiers]
    for i, quest in enumerate(pack.quests):
        loc = ("quests", i)
        for j, reward in enumerate(quest.xp):
            need_skill(reward.skill, (*loc, "xp", j, "skill"))
        if not known(quest.kind, vocab.QUEST_KINDS, "a quest kind", (*loc, "kind")):
            continue
        quest_kind = vocab.QUEST_KINDS[quest.kind]
        if len(quest.steps) != quest_kind.steps:
            problems.append(
                Problem(
                    (*loc, "steps"), "steps", f"this kind of quest has {quest_kind.steps} steps, not {len(quest.steps)}"
                )
            )
        param_problems = _check_params(quest_kind.params, quest.params, (*loc, "params"))
        problems += param_problems
        names = set(quest.params)
        for j, step in enumerate(quest.steps):
            problems += _check_text(step, names, (*loc, "steps", j), quest.params)
        problems += _check_text(quest.reward, names, (*loc, "reward"), quest.params)
        if quest_kind.capstone:
            if quest.requirement is None:
                problems.append(
                    Problem((*loc, "requirement"), "missing", "a capstone states its total-level requirement")
                )
            else:
                problems += _check_text(quest.requirement, names, (*loc, "requirement"), quest.params)
            if quest.xp:
                problems.append(
                    Problem((*loc, "xp"), "capstone", "a capstone gives no XP: its requirement is read from the levels")
                )
            if not param_problems and quest.params["diary_tier"] not in diary_tiers:
                problems.append(Problem((*loc, "params", "diary_tier"), "unknown", "no diary tier has this id"))
        else:
            if quest.requirement is not None:
                problems.append(Problem((*loc, "requirement"), "capstone", "only a capstone has a requirement"))
            if quest.cloak_trim:
                problems.append(Problem((*loc, "cloak_trim"), "capstone", "only a capstone trims the cloaks"))

    for i, diary_tier in enumerate(pack.diaries.tiers):
        for j, task in enumerate(diary_tier.tasks):
            loc = ("diaries", "tiers", i, "tasks", j)
            if not known(task.kind, vocab.TASK_KINDS, "a diary task kind", (*loc, "kind")):
                continue
            if task.kind == "all_above" and i == 0:
                problems.append(Problem((*loc, "kind"), "all_above", "the first tier has no tier above it"))
            problems += _check_params(vocab.TASK_KINDS[task.kind], task.params, (*loc, "params"))
            problems += _check_text(task.text, set(task.params), (*loc, "text"), task.params)

    for name, allowed in vocab.MESSAGE_PLACEHOLDERS.items():
        problems += _check_text(getattr(pack.messages, name), allowed, ("messages", name))
    for name, allowed in vocab.LABEL_PLACEHOLDERS.items():
        problems += _check_text(getattr(pack.labels, name), allowed, ("labels", name))
    return problems


def warnings(pack: Pack) -> list[str]:
    """What a valid pack leaves out without being wrong: categories no skill is trained by."""
    trained = {key for skill in pack.skills for key in skill.trained_by}
    return [f"{key} trains no skill: its calls earn nothing" for key in sorted(vocab.ACTION_KEYS - trained)]


# ---- the shipped default ----


@cache
def default_text() -> str:
    """The shipped default pack, as the file in the tree reads."""
    return resources.files("embry0.services").joinpath(DEFAULT_PACK_FILE).read_text(encoding="utf-8")


@cache
def default_pack() -> Pack:
    """The shipped default pack. A default that does not validate is a bug, so this raises."""
    return parse_text(default_text())


def pack_hash(pack: Pack) -> str:
    """A pack's identity: the same values give the same hash, whatever the comments or layout."""
    canonical = json.dumps(pack.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class PackSource(Protocol):
    """Where the active pack is read; the progression service asks it on every recompute."""

    async def active(self) -> Pack: ...


class ShippedPack:
    """The pack source that always answers the shipped default."""

    async def active(self) -> Pack:
        return default_pack()


# ---- rules: a pack as lookups ----


def fact_key(metric: str, params: dict[str, Any]) -> str:
    """The key a metric's steps are filed under: its id, plus its parameters when it has any."""
    model = vocab.METRICS[metric].params.model_validate(params)
    values = vocab.dump_params(model)
    return metric if not values else f"{metric}{json.dumps(values, sort_keys=True, separators=(',', ':'))}"


@dataclass(frozen=True)
class FactQuery:
    """One fragment of the facts query: ``key`` its steps are filed under, ``fragment`` the SQL, its parameters."""

    key: str
    fragment: str
    params: dict[str, Any]


@dataclass(frozen=True)
class OutcomeRule:
    skill: str
    xp: int
    per_stale_claim: int = 0
    per_line_removed: int = 0
    max_lines: int = 0


@dataclass(frozen=True)
class AchievementRule:
    """An achievement as the services evaluate it: the pack's entry, its typed parameters and its fact key."""

    entry: Achievement
    params: vocab.Params
    key: str

    @property
    def id(self) -> str:
        return self.entry.id

    @property
    def objective(self) -> bool:
        return len(self.entry.thresholds) == 1


@dataclass(frozen=True)
class Rules:
    """A pack turned into the lookups the services price, level and evaluate with."""

    pack: Pack
    hash: str
    zone: ZoneInfo
    xp_table: tuple[int, ...]
    skill_ids: tuple[str, ...]
    skill_for_action: dict[str, str]
    action_xp: dict[str, int]
    outcomes: dict[str, OutcomeRule]
    run_outcomes: dict[str, tuple[str, int]]
    tier_ids: tuple[str, ...]
    rare_on_date: dict[tuple[int, int], str]
    rare_first_run_of_year: str | None
    achievements: tuple[AchievementRule, ...]
    fact_queries: tuple[FactQuery, ...]
    _tier_starts: tuple[int, ...] = field(repr=False)

    @property
    def max_level(self) -> int:
        return self.pack.curve.max_level

    def xp_for_level(self, level: int) -> int:
        """The experience ``level`` starts at, clamped to 1..max."""
        return self.xp_table[max(1, min(level, self.max_level)) - 1]

    def level_for_xp(self, xp: int) -> int:
        """The level ``xp`` experience reaches, 1 to max."""
        return min(bisect_right(self.xp_table, max(xp, 0)), self.max_level)

    def tier_for_level(self, level: int) -> Tier:
        """The tier a character whose best skill is at ``level`` wears."""
        return self.pack.tiers[max(bisect_right(self._tier_starts, level) - 1, 0)]

    def character_level(self, levels: list[int]) -> int:
        """A character's one level, from its five best skills (missing ones count as level 1).

        The two best weigh 0.325 each and the next three 0.25 each, the fifth
        counting half, so depth and breadth both count.
        """
        top = sorted(levels, reverse=True) + [1] * max(0, 5 - len(levels))
        return math.floor(0.25 * (top[2] + top[3] + top[4] // 2) + 0.325 * (top[0] + top[1]))

    @property
    def character_max_level(self) -> int:
        return self.character_level([self.max_level] * len(self.skill_ids))

    def local(self, at: datetime) -> datetime:
        """``at`` on the pack's calendar; a naive time is UTC."""
        return (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(self.zone)

    def view(self) -> dict[str, Any]:
        """What GET /progression carries of the pack itself: its names, colours, words and art."""
        pack = self.pack
        return {
            "pack": {"hash": self.hash, "version": pack.version, "timezone": pack.timezone},
            "groups": [{"id": g.id, "name": g.name} for g in pack.groups],
            "skills": [
                {
                    "id": s.id,
                    "name": s.name,
                    "group": s.group,
                    "cloak": s.cloak.model_dump() if s.cloak else dict(NEUTRAL_CLOAK),
                }
                for s in pack.skills
            ],
            "tiers": [t.model_dump() for t in pack.tiers],
            "rares": [{"id": r.id, "name": r.name, "art": r.art} for r in pack.rares],
            "cloaks": {"quest": pack.cloaks.quest.model_dump()},
            "achievement_categories": [c.model_dump() for c in pack.achievement_categories],
            "diary_tiers": [{"id": t.id, "name": t.name} for t in pack.diaries.tiers],
            "messages": pack.messages.model_dump(),
            "labels": pack.labels.model_dump(),
            "curve": {"max_level": self.max_level, "character_max_level": self.character_max_level},
        }


def _rules(pack: Pack) -> Rules:
    achievements: list[AchievementRule] = []
    queries: dict[str, FactQuery] = {}
    for entry in pack.achievements:
        metric = vocab.METRICS[entry.metric]
        params = metric.params.model_validate(entry.params)
        key = fact_key(entry.metric, entry.params)
        achievements.append(AchievementRule(entry, params, key))
        if metric.fragment is not None:
            # A context audit is one fact, split into its claims or a clean audit when it is read.
            query_key = "audit" if metric.fragment == "audit" else key
            # Every parameter, its defaults included: the fragment binds each one by name.
            queries.setdefault(query_key, FactQuery(query_key, metric.fragment, params.model_dump(mode="json")))
    outcomes: dict[str, OutcomeRule] = {}
    for source in vocab.OUTCOME_SOURCES:
        outcome = getattr(pack.rates.outcomes, source)
        if outcome is not None:
            outcomes[source] = OutcomeRule(
                outcome.skill,
                outcome.xp,
                per_stale_claim=getattr(outcome, "per_stale_claim", 0),
                per_line_removed=getattr(outcome, "per_line_removed", 0),
                max_lines=getattr(outcome, "max_lines", 0),
            )
    rare_on_date: dict[tuple[int, int], str] = {}
    yearly: str | None = None
    for rare in pack.rares:
        if rare.when.merge_on is not None:
            month, day = (int(part) for part in rare.when.merge_on.split("-"))
            rare_on_date[(month, day)] = rare.id
        elif rare.when.first_run_of_year:
            yearly = rare.id
    return Rules(
        pack=pack,
        hash=pack_hash(pack),
        zone=ZoneInfo(pack.timezone),
        xp_table=_xp_table(pack.curve),
        skill_ids=tuple(s.id for s in pack.skills),
        skill_for_action={key: s.id for s in pack.skills for key in s.trained_by},
        action_xp=dict(pack.rates.actions),
        outcomes=outcomes,
        run_outcomes={r.agent: (r.skill, r.xp) for r in pack.rates.runs},
        tier_ids=tuple(t.id for t in pack.tiers),
        rare_on_date=rare_on_date,
        rare_first_run_of_year=yearly,
        achievements=tuple(achievements),
        fact_queries=tuple(queries.values()),
        _tier_starts=tuple(t.from_level for t in pack.tiers),
    )


_RULES: dict[str, Rules] = {}


def rules_for(pack: Pack) -> Rules:
    """The rules of a valid pack, built once per distinct pack."""
    digest = pack_hash(pack)
    rules = _RULES.get(digest)
    if rules is None:
        if len(_RULES) > 16:
            _RULES.clear()
        rules = _RULES[digest] = _rules(pack)
    return rules


def default_rules() -> Rules:
    return rules_for(default_pack())


# ---- the contract the frontend pins ----


def contract() -> dict[str, Any]:
    """``docs/contracts/progression.json``: the art the frontend draws, and the shipped default as the API serves it."""
    return {
        "art": {
            "gear_steps": list(vocab.GEAR_STEPS),
            "rare_art": list(vocab.RARE_ART),
            "achievement_icons": list(vocab.ACHIEVEMENT_ICONS),
        },
        "action_keys": sorted(vocab.ACTION_KEYS),
        "default_view": default_rules().view(),
        # The template grammar's cases: the server's render and the dashboard's fill must both give `out`.
        "template_cases": [{"text": t, "values": v, "out": o} for t, v, o in templates.CASES],
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-contract", metavar="PATH", help="write docs/contracts/progression.json")
    parser.add_argument("--check", metavar="FILE", help="validate a pack file and print every problem")
    args = parser.parse_args(argv)
    if args.write_contract:
        Path(args.write_contract).write_text(json.dumps(contract(), indent=2, sort_keys=True) + "\n")
        return 0
    if args.check:
        try:
            pack = parse_text(Path(args.check).read_text(encoding="utf-8"))
        except PackInvalid as exc:
            for problem in exc.problems:
                print(problem, file=sys.stderr)
            return 1
        for warning in warnings(pack):
            print(f"warning: {warning}", file=sys.stderr)
        print("the pack is valid")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
