"""The slop score: weekly attention cost against attention spent (spec 2026-10-26, slop-o-meter).

A clean-room reimplementation of Slop-O-Meter's published slop score algorithm
(https://github.com/slop-o-meter/slop-o-meter, docs/slop-score-algorithm.md),
written from its description alone. Each week's cost is its net weighted
additions, dampened by the codebase's size; its capacity is the human session
time behind it, as lines; the excess is slop, and the score is the history's
slop over its cost. Pure functions over parsed rows: no git, no network.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from embry0.services.slop_meter.history import Commit, is_bot

BOOTSTRAP_THRESHOLD = 5_000
COMPLEXITY_WEIGHT = 0.05
BASE_NEIGHBORHOOD = 1.0
MARGINAL_HOURS_PER_SUBCOMMIT = 1.0
AI_CO_AUTHOR_ATTENTION_WEIGHT = 0.8
CO_AUTHOR_WEIGHTS = (0.5, 0.25, 0.125)
LINES_PER_HOUR = 40.0
CORE_RAMP_START = 10
CORE_RAMP_END = 60
WEEKLY_HOURS_CAP = 80.0
OUTLIER_MIN_ADDITIONS = 2_000
OUTLIERS_PER_YEAR = 4

#: (upper bound, level, name), the score's interpretation.
LEVELS: tuple[tuple[float, int, str], ...] = (
    (0.2, 1, "Buttered Noodles"),
    (0.4, 2, "Spaghetti Bolognese"),
    (0.6, 3, "Lasagna"),
    (0.8, 4, "Sloppy Joe"),
    (math.inf, 5, "Just The Slop"),
)
_YEAR = timedelta(days=365.25)


def level_of(score: float) -> tuple[int, str]:
    for bound, level, name in LEVELS:
        if score < bound:
            return level, name
    return LEVELS[-1][1], LEVELS[-1][2]


def week_of(at: datetime) -> date:
    """The Monday (UTC) of ``at``'s week."""
    day = at.astimezone(UTC).date()
    return day - timedelta(days=day.weekday())


def size_dampening(cumulative_lines: float) -> float:
    """A line's weight by the codebase's size: a bootstrap ramp to 5,000 lines plus a slow log bonus."""
    lines = max(0.0, cumulative_lines)
    return min(1.0, lines / BOOTSTRAP_THRESHOLD) + COMPLEXITY_WEIGHT * math.log2(1 + lines / 100_000)


def capped_hours(hours: float) -> float:
    """Effective hours under the concave overtime curve: ``h − h³/(3·cap²)``, flat past the cap."""
    h = min(max(0.0, hours), WEEKLY_HOURS_CAP)
    return h - h**3 / (3 * WEEKLY_HOURS_CAP**2)


def core_factor(commits: int, total_commits: int) -> float:
    """How much of a contributor's time counts: 0 below 10 commits, 1 at 60, up to 1.25× for a large share."""
    ramp = min(1.0, max(0.0, (commits - CORE_RAMP_START) / (CORE_RAMP_END - CORE_RAMP_START)))
    share = commits / total_commits if total_commits else 0.0
    return ramp * (1 + 0.25 * share)


# ---- attention spent ----


@dataclass(frozen=True)
class Event:
    """A forge event a person made: a comment, a review comment or a review."""

    login: str
    at: datetime
    kind: str = "comment"


def _merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _hours_by_week(sessions: list[tuple[datetime, datetime]]) -> dict[date, float]:
    """Each week's share of the sessions, in hours (a session across a week boundary is split)."""
    out: dict[date, float] = defaultdict(float)
    for start, end in sessions:
        cursor = start
        while cursor < end:
            monday = week_of(cursor)
            boundary = datetime(monday.year, monday.month, monday.day, tzinfo=UTC) + timedelta(days=7)
            stop = min(end, boundary)
            out[monday] += (stop - cursor).total_seconds() / 3600
            cursor = stop
    return out


def contributor_of(login: str, logins: Mapping[str, str]) -> str:
    """The contributor key of a forge login: its email when known, else the login itself."""
    email = logins.get(login.lower())
    return email.lower() if email else f"login:{login.lower()}"


def weekly_capacity(
    commits: Sequence[Commit], events: Iterable[Event], logins: Mapping[str, str]
) -> dict[str, dict[date, float]]:
    """Each contributor's attention spent per week, in lines (already capped and core-weighted)."""
    neighborhoods: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    authored: dict[str, int] = defaultdict(int)
    for c in commits:
        if c.bot:
            continue
        authored[c.author] += 1
        hours = BASE_NEIGHBORHOOD + c.subcommits * MARGINAL_HOURS_PER_SUBCOMMIT
        if c.ai_coauthor:
            hours *= AI_CO_AUTHOR_ATTENTION_WEIGHT
        neighborhoods[c.author].append((c.at - timedelta(hours=hours), c.at))
        for i, (_name, email) in enumerate(c.coauthors):
            weight = CO_AUTHOR_WEIGHTS[min(i, len(CO_AUTHOR_WEIGHTS) - 1)]
            neighborhoods[email.lower()].append((c.at - timedelta(hours=BASE_NEIGHBORHOOD * weight), c.at))
    for e in events:
        if is_bot(e.login, ""):
            continue
        neighborhoods[contributor_of(e.login, logins)].append((e.at - timedelta(hours=BASE_NEIGHBORHOOD), e.at))
    total = sum(authored.values())
    out: dict[str, dict[date, float]] = {}
    for who, spans in neighborhoods.items():
        factor = core_factor(authored.get(who, 0), total)
        if factor <= 0:
            continue
        out[who] = {
            week: capped_hours(hours * factor) * LINES_PER_HOUR for week, hours in _hours_by_week(_merge(spans)).items()
        }
    return out


# ---- the walk ----


@dataclass(frozen=True)
class Week:
    week: date
    cost: float
    capacity: float
    slop: float
    #: The scope's cumulative weighted size at the week's end.
    lines: float
    #: The cumulative score at the week's end.
    score: float


@dataclass
class Meter:
    score: float
    level: int
    name: str
    #: An outlier commit is not judged yet: its lines are left out of the slop until it is.
    provisional: bool
    weeks: list[Week] = field(default_factory=list)
    commits: int = 0


@dataclass(frozen=True)
class Outlier:
    commit: Commit
    net: float
    #: True slop, False not slop, None not judged yet.
    slop: bool | None
    #: The codebase's weighted size at its week, and the project's age at the commit.
    size_at: float
    age_days: int


def commit_lines(commit: Commit, weigh: Callable[[str], float]) -> tuple[float, float]:
    add = sum(a * weigh(path) for path, a, _d in commit.files)
    delete = sum(d * weigh(path) for path, _a, d in commit.files)
    return add, delete


def outliers(commits: Sequence[Commit], weigh: Callable[[str], float], judgments: Mapping[str, bool]) -> list[Outlier]:
    """The top ``⌈4 × age in years⌉`` commits of at least 2,000 net weighted additions, with what is known of them.

    The repository's first two commits are never slop; the rest are what the
    model judged (``judgments``), or None while not judged.
    """
    if not commits:
        return []
    first, last = commits[0].at, commits[-1].at
    count = math.ceil(OUTLIERS_PER_YEAR * (last - first) / _YEAR)
    nets: list[tuple[float, int, Commit]] = []
    for index, c in enumerate(commits):
        add, delete = commit_lines(c, weigh)
        net = max(0.0, add - delete)
        if net >= OUTLIER_MIN_ADDITIONS:
            nets.append((net, index, c))
    top = sorted(nets, key=lambda item: -item[0])[:count]
    sizes = _sizes_by_week(commits, weigh)
    out = []
    for net, index, c in sorted(top, key=lambda item: item[1]):
        slop = False if index < 2 else judgments.get(c.sha)
        out.append(Outlier(c, net, slop, sizes.get(week_of(c.at), 0.0), (c.at - first).days))
    return out


def _sizes_by_week(commits: Sequence[Commit], weigh: Callable[[str], float]) -> dict[date, float]:
    sizes: dict[date, float] = {}
    size = 0.0
    for c in commits:
        add, delete = commit_lines(c, weigh)
        size = max(0.0, size + add - delete)
        sizes[week_of(c.at)] = size
    return sizes


def meters(
    commits: Sequence[Commit],
    events: Iterable[Event],
    logins: Mapping[str, str],
    weigh: Callable[[str], float],
    judgments: Mapping[str, bool],
    area_of: Callable[[str], str | None] = lambda _path: None,
) -> dict[str, Meter]:
    """The repository's meter (key ``""``) and each area's, from the history oldest first.

    ``area_of`` names a path's area (None for none). An area's dampening uses
    the repository's size; its capacity is each contributor's weekly capacity
    apportioned by the share of their weighted churn that week in the area
    (their history-wide share in a week without commits of their own).
    """
    commits = sorted(commits, key=lambda c: c.at)
    if not commits:
        return {"": Meter(0.0, 1, LEVELS[0][2], False)}
    found = outliers(commits, weigh, judgments)
    left_out = {o.commit.sha for o in found if o.slop is not True}
    provisional = any(o.slop is None for o in found)

    adds: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    dels: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    excluded: dict[str, dict[date, float]] = defaultdict(lambda: defaultdict(float))
    churn: dict[str, dict[tuple[str, date], float]] = defaultdict(lambda: defaultdict(float))
    churn_all: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    area_cache: dict[str, str | None] = {}
    for c in commits:
        week = week_of(c.at)
        per_scope: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for path, added, deleted in c.files:
            w = weigh(path)
            if not w:
                continue
            if path not in area_cache:
                area_cache[path] = area_of(path)
            scopes = [""]
            area = area_cache[path]
            if area:
                scopes.append(area)
            for scope in scopes:
                per_scope[scope][0] += added * w
                per_scope[scope][1] += deleted * w
        for scope, (scope_add, scope_del) in per_scope.items():
            adds[scope][week] += scope_add
            dels[scope][week] += scope_del
            if c.sha in left_out:
                excluded[scope][week] += max(0.0, scope_add - scope_del)
            if not c.bot:
                churn[scope][(c.author, week)] += scope_add + scope_del
                churn_all[scope][c.author] += scope_add + scope_del

    capacity_by_who = weekly_capacity(commits, events, logins)
    first_week = week_of(commits[0].at)
    last_week = week_of(commits[-1].at)
    weeks = [first_week + timedelta(weeks=i) for i in range((last_week - first_week).days // 7 + 1)]

    # The repository's size, which dampens every scope's lines.
    damp: dict[date, float] = {}
    size = 0.0
    for week in weeks:
        size = max(0.0, size + adds[""][week] - dels[""][week])
        damp[week] = size_dampening(size)

    out: dict[str, Meter] = {}
    for scope in adds:
        series: list[Week] = []
        cum_cost = cum_slop = scope_size = 0.0
        for week in weeks:
            week_add, week_del = adds[scope][week], dels[scope][week]
            scope_size = max(0.0, scope_size + week_add - week_del)
            net = max(0.0, week_add - week_del)
            cost = net * damp[week]
            if scope == "":
                capacity = sum(per_week.get(week, 0.0) for per_week in capacity_by_who.values())
            else:
                capacity = 0.0
                for who, per_week in capacity_by_who.items():
                    lines = per_week.get(week, 0.0)
                    if not lines:
                        continue
                    mine = churn[""].get((who, week), 0.0)
                    if mine:
                        share = churn[scope].get((who, week), 0.0) / mine
                    else:
                        total = churn_all[""].get(who, 0.0)
                        share = churn_all[scope].get(who, 0.0) / total if total else 0.0
                    capacity += lines * share
            counted = max(0.0, net - excluded[scope][week]) * damp[week]
            slop = max(0.0, counted - capacity)
            cum_cost += cost
            cum_slop += slop
            series.append(Week(week, cost, capacity, slop, scope_size, cum_slop / cum_cost if cum_cost else 0.0))
        score = cum_slop / cum_cost if cum_cost else 0.0
        level, name = level_of(score)
        out[scope] = Meter(score, level, name, provisional, series, len(commits))
    out.setdefault("", Meter(0.0, 1, LEVELS[0][2], provisional, [], len(commits)))
    return out
