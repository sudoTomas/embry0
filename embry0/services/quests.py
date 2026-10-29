"""Quests and repo diaries (spec 2026-10-24): goals the account completes, recomputed from history.

A quest is a few steps the account completes once, for quest points and an
XP reward to the characters who did the work; a repo diary is a checklist
per repository in tiers. Both are the active content pack's (#176): a quest
is one of the kinds below with the pack's name, texts, parameters and
rewards, and a diary tier is a list of tasks, each a task kind with its
parameters. Like experience and achievements they are read from rows other
features already write, never stored: each step and task is done at the
time of the row that first met it.

A job has two times (#216). A step or task about a job's own work (it
finished, failed, ran QA or audited) reads when the job finished
(``JobRow.finished_at``); one about a merge reads when its pull request
merged (``JobRow.merged_at``). Neither moves when merge sync later records
the pull request opened, closed or reopened, so a task done stays done at
the time it was done.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from embry0.services import progression_templates as templates
from embry0.services.context_audit import report_counts
from embry0.services.progression_pack import DiaryTier as DiaryTierEntry
from embry0.services.progression_pack import Quest as QuestEntry
from embry0.services.progression_pack import Rules
from embry0.services.progression_vocabulary import QUEST_KINDS
from embry0.storage.repositories.progression import FINISHED_WELL, JobRow, Journal, RunRow

#: The parameters, of a quest kind or a diary task kind, that name the agents whose runs it reads.
#: Every kind that reads ``Journal.runs`` keeps only the runs of the agents these name.
RUN_AGENT_PARAMS = ("agents", "review_agents", "qa_agents")


def run_agents(rules: Rules) -> frozenset[str]:
    """Every agent whose runs the pack's quests and diary tasks read: the journal needs no other run."""
    params: list[dict[str, Any]] = [quest.params for quest in rules.pack.quests]
    params += [task.params for tier in rules.pack.diaries.tiers for task in tier.tasks]
    return frozenset(agent for p in params for name in RUN_AGENT_PARAMS for agent in p.get(name, ()))


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def _nth(times: Iterable[datetime], n: int) -> datetime | None:
    """When the n-th of these happened, or None before it has."""
    ordered = sorted(times)
    return ordered[n - 1] if len(ordered) >= n else None


def _first(times: Iterable[datetime]) -> datetime | None:
    return _nth(times, 1)


def _merges(jobs: Iterable[JobRow]) -> list[tuple[JobRow, datetime]]:
    """Every merged job, with when it merged: the time a step or task about a merge reads."""
    return [(job, at) for job in jobs if (at := job.merged_at) is not None]


def _when_all(times: Sequence[datetime | None]) -> datetime | None:
    """When the last of these was done, or None while any is not."""
    if any(t is None for t in times):
        return None
    return max(t for t in times if t is not None)


@dataclass(frozen=True)
class Task:
    text: str
    done_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "done_at": _iso(self.done_at)}


# ---- repo diaries ----


@dataclass
class DiaryTier:
    tier: str
    tasks: list[Task]

    @property
    def completed_at(self) -> datetime | None:
        return _when_all([t.done_at for t in self.tasks])

    def to_dict(self) -> dict[str, Any]:
        return {"tier": self.tier, "tasks": [t.to_dict() for t in self.tasks], "completed_at": _iso(self.completed_at)}


@dataclass
class Diary:
    repo: str
    tiers: list[DiaryTier]

    def tier(self, name: str) -> DiaryTier:
        return next(t for t in self.tiers if t.tier == name)

    @property
    def started(self) -> bool:
        return any(task.done_at for tier in self.tiers for task in tier.tasks)

    def to_dict(self) -> dict[str, Any]:
        return {"repo": self.repo, "tiers": [t.to_dict() for t in self.tiers]}


def _quiet_stretch(jobs: Sequence[JobRow], days: int) -> datetime | None:
    """The first finished job a full quiet stretch after the repo's last failure, or after its first job.

    The jobs are walked in the order they finished: a pull request merging
    later does not move a job past a failure that came after it (#216).
    """
    since: datetime | None = None
    for job in sorted(jobs, key=lambda j: j.finished_at):
        if since is None:
            since = job.created_at
        if job.status == "failed":
            since = job.finished_at
        elif job.status in FINISHED_WELL and job.finished_at - since >= timedelta(days=days):
            return job.finished_at
    return None


@dataclass(frozen=True)
class _RepoRows:
    """One repository's jobs and successful runs, what its diary tasks read."""

    jobs: Sequence[JobRow]
    runs: Sequence[RunRow]

    @property
    def merges(self) -> list[datetime]:
        return [at for _, at in _merges(self.jobs)]

    @property
    def audits(self) -> list[JobRow]:
        return [j for j in self.jobs if j.template == "context-audit" and j.status in FINISHED_WELL]

    def agent_runs(self, params: dict[str, Any]) -> list[RunRow]:
        agents = set(params["agents"])
        return [r for r in self.runs if r.agent_type in agents]


def _pass_qa(rows: _RepoRows, params: dict[str, Any], below: datetime | None) -> datetime | None:
    qa_issues = {r.issue_id for r in rows.agent_runs(params) if r.issue_id}
    passes = [j.finished_at for j in rows.jobs if j.template == "qa" and j.status in FINISHED_WELL]
    passes += [at for j, at in _merges(rows.jobs) if j.issue_id in qa_issues]
    return _first(passes)


_TaskEvaluator = Callable[[_RepoRows, dict[str, Any], datetime | None], datetime | None]

#: Each diary task kind (``progression_vocabulary.TASK_KINDS``): when it was first done for one repository.
#: ``below`` is when every tier above the task's own was done.
_TASKS: dict[str, _TaskEvaluator] = {
    "triage": lambda rows, params, below: _first(r.at for r in rows.agent_runs(params) if r.issue_id),
    "finish_job": lambda rows, params, below: _first(j.finished_at for j in rows.jobs if j.status in FINISHED_WELL),
    "run_qa": lambda rows, params, below: _first(
        [r.at for r in rows.agent_runs(params)]
        + [j.finished_at for j in rows.jobs if j.template == "qa" and j.status in FINISHED_WELL]
    ),
    "merges": lambda rows, params, below: _nth(rows.merges, int(params["count"])),
    "pass_qa": _pass_qa,
    "audit": lambda rows, params, below: _first(j.finished_at for j in rows.audits),
    "clean_audit": lambda rows, params, below: _first(
        j.finished_at for j in rows.audits if report_counts(j.report)[0] == 0
    ),
    "reviews": lambda rows, params, below: _nth((r.at for r in rows.agent_runs(params)), int(params["count"])),
    "quiet_stretch": lambda rows, params, below: _quiet_stretch(rows.jobs, int(params["days"])),
    "all_above": lambda rows, params, below: below,
}


def _diary(repo: str, jobs: Sequence[JobRow], runs: Sequence[RunRow], tiers: Sequence[DiaryTierEntry]) -> Diary:
    rows = _RepoRows(jobs, runs)
    done: list[DiaryTier] = []
    for entry in tiers:
        below = _when_all([t.completed_at for t in done]) if done else None
        tasks = [
            Task(templates.render(task.text, task.params), _TASKS[task.kind](rows, task.params, below))
            for task in entry.tasks
        ]
        done.append(DiaryTier(entry.id, tasks))
    return Diary(repo, done)


def evaluate_diaries(journal: Journal, *, rules: Rules) -> list[Diary]:
    """A diary for every repository with a job, those with the most tiers done first."""
    jobs: dict[str, list[JobRow]] = defaultdict(list)
    runs: dict[str, list[RunRow]] = defaultdict(list)
    for job in journal.jobs:
        jobs[job.repo].append(job)
    for run in journal.runs:
        runs[run.repo].append(run)
    tiers = rules.pack.diaries.tiers
    diaries = [_diary(repo, jobs[repo], runs[repo], tiers) for repo in sorted(jobs)]

    def done(d: Diary) -> int:
        return sum(1 for t in d.tiers if t.completed_at) * 100 + sum(
            1 for t in d.tiers for task in t.tasks if task.done_at
        )

    return sorted(diaries, key=lambda d: (-done(d), d.repo))


# ---- quests ----


@dataclass(frozen=True)
class Reward:
    """XP a quest gives a character when it completes."""

    agent_type: str
    stat: str
    xp: int


@dataclass
class QuestState:
    id: str
    name: str
    points: int
    reward: str
    steps: list[Task]
    #: A requirement before the quest can complete, and when it was met.
    requirement: str | None = None
    requirement_met_at: datetime | None = None
    #: Where the steps happened, when they belong to one repository or issue.
    where: str | None = None
    xp: list[Reward] = field(default_factory=list)
    #: Done, this quest trims every cloak (a capstone's reward).
    cloak_trim: bool = False

    @property
    def completed_at(self) -> datetime | None:
        times = [s.done_at for s in self.steps]
        if self.requirement is not None:
            times.append(self.requirement_met_at)
        return _when_all(times)

    @property
    def state(self) -> str:
        if self.completed_at:
            return "complete"
        if any(s.done_at for s in self.steps):
            return "in_progress"
        return "not_started"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "points": self.points,
            "reward": self.reward,
            "state": self.state,
            "requirement": self.requirement,
            "requirement_met": self.requirement is None or self.requirement_met_at is not None,
            "where": self.where,
            "steps": [s.to_dict() for s in self.steps],
            "completed_at": _iso(self.completed_at),
        }


def _best(candidates: Iterable[tuple[str, list[datetime | None]]]) -> tuple[str | None, list[datetime | None]]:
    """The candidate furthest along: the most steps done, then the earliest to finish them."""
    best: tuple[str | None, list[datetime | None]] = (None, [])
    best_key: tuple[int, datetime] | None = None
    for where, times in candidates:
        done = [t for t in times if t is not None]
        key = (-len(done), max(done) if done else datetime.max.replace(tzinfo=UTC))
        if best_key is None or key < best_key:
            best, best_key = (where, times), key
    return best


def _onboard_to_merge(journal: Journal, params: dict[str, Any]) -> tuple[str | None, list[datetime | None]]:
    by_repo: dict[str, list[JobRow]] = defaultdict(list)
    for job in journal.jobs:
        by_repo[job.repo].append(job)
    candidates = []
    for repo, jobs in by_repo.items():
        onboarded = _first(j.finished_at for j in jobs if j.template == "onboard" and j.status in FINISHED_WELL)
        if onboarded is None:
            continue
        after = [j for j in jobs if j.created_at > onboarded]
        finished = _first(j.finished_at for j in after if j.status in FINISHED_WELL)
        merged = _first(at for _, at in _merges(after))
        candidates.append((repo, [onboarded, finished, merged]))
    where, times = _best(candidates)
    return where, times or [None, None, None]


def _merge_after_failure(journal: Journal, params: dict[str, Any]) -> tuple[str | None, list[datetime | None]]:
    """A failure on an issue, then a merge of a later job on it: the comeback rule (#234).

    A merged job counts when a failed job on its issue was created before it,
    as for the comeback XP and the achievement that counts comebacks
    (``comeback`` in ``embry0/storage/repositories/progression.py``). The
    failure step is that failure, and the merge step is done once both
    happened: a retry that merges before the job it retried fails is done
    when the failure lands.
    """
    failures: dict[str, list[JobRow]] = defaultdict(list)
    for job in journal.jobs:
        if job.issue_id and job.status == "failed":
            failures[job.issue_id].append(job)
    comebacks: list[tuple[str, datetime, datetime]] = []
    for job, merged_at in _merges(journal.jobs):
        if not job.issue_id:
            continue
        failed_at = _first(f.finished_at for f in failures[job.issue_id] if f.created_at < job.created_at)
        if failed_at is not None:
            comebacks.append((job.issue_id, failed_at, max(failed_at, merged_at)))
    if not comebacks:
        return None, [_first(f.finished_at for jobs in failures.values() for f in jobs), None]
    issue, failed_at, done_at = min(comebacks, key=lambda c: c[2])
    return issue, [failed_at, done_at]


def _review_qa_merge(journal: Journal, params: dict[str, Any]) -> tuple[str | None, list[datetime | None]]:
    review_agents, qa_agents = set(params["review_agents"]), set(params["qa_agents"])
    reviewed: dict[str, datetime] = {}
    tested: dict[str, datetime] = {}
    merged: dict[str, datetime] = {}
    for run in sorted(journal.runs, key=lambda r: r.at):
        if run.issue_id and run.agent_type in review_agents:
            reviewed.setdefault(run.issue_id, run.at)
        if run.issue_id and run.agent_type in qa_agents:
            tested.setdefault(run.issue_id, run.at)
    for job, at in sorted(_merges(journal.jobs), key=lambda merge: merge[1]):
        if job.issue_id:
            merged.setdefault(job.issue_id, at)
    issues = set(reviewed) | set(tested) | set(merged)
    where, times = _best((i, [reviewed.get(i), tested.get(i), merged.get(i)]) for i in issues)
    return where, times or [None, None, None]


#: Each quest kind's evaluator but the capstone's, which reads the diaries and the levels (``_capstone``).
_KINDS: dict[str, Callable[[Journal, dict[str, Any]], tuple[str | None, list[datetime | None]]]] = {
    "onboard_to_merge": _onboard_to_merge,
    "merge_after_failure": _merge_after_failure,
    "review_qa_merge": _review_qa_merge,
}


def _quest(entry: QuestEntry, where: str | None, times: Sequence[datetime | None]) -> QuestState:
    return QuestState(
        entry.id,
        entry.name,
        entry.points,
        templates.render(entry.reward, entry.params),
        [Task(templates.render(text, entry.params), done_at) for text, done_at in zip(entry.steps, times, strict=True)],
        where=where,
        xp=[Reward(r.agent, r.skill, r.xp) for r in entry.xp],
        cloak_trim=entry.cloak_trim,
    )


def _capstone(
    entry: QuestEntry,
    journal: Journal,
    diaries: Sequence[Diary],
    total_reached: Callable[[int], datetime | None],
) -> QuestState:
    params = entry.params
    tier = str(params["diary_tier"])
    completed = _first(t for d in diaries if (t := d.tier(tier).completed_at) is not None)
    merges = _nth((at for _, at in _merges(journal.jobs)), int(params["merges"]))
    quest = _quest(entry, None, [merges, completed])
    quest.requirement = templates.render(entry.requirement or "", params)
    quest.requirement_met_at = total_reached(int(params["total_level"]))
    return quest


def _rewarding(journal: Journal, rules: Rules) -> list[QuestState]:
    """Every quest whose kind gives XP, in the pack's order."""
    return [
        _quest(e, *_KINDS[e.kind](journal, e.params)) for e in rules.pack.quests if not QUEST_KINDS[e.kind].capstone
    ]


def quest_rewards(journal: Journal, *, rules: Rules) -> list[tuple[datetime, Reward]]:
    """Each XP reward of a completed quest, at its completion: computed before levels, which they feed."""
    out: list[tuple[datetime, Reward]] = []
    for quest in _rewarding(journal, rules):
        if quest.completed_at is not None:
            out.extend((quest.completed_at, reward) for reward in quest.xp)
    return out


def evaluate_quests(
    journal: Journal,
    diaries: Sequence[Diary],
    total_reached: Callable[[int], datetime | None],
    *,
    rules: Rules,
) -> list[QuestState]:
    """Every quest's state, in the pack's order. ``total_reached(n)`` is when a character first reached total level n."""
    out: list[QuestState] = []
    for entry in rules.pack.quests:
        if QUEST_KINDS[entry.kind].capstone:
            out.append(_capstone(entry, journal, diaries, total_reached))
        else:
            out.append(_quest(entry, *_KINDS[entry.kind](journal, entry.params)))
    return out
