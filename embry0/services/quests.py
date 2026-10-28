"""Quests and repo diaries (spec 2026-10-24): goals the account completes, recomputed from history.

A quest is a few steps the account completes once, for quest points and an
XP reward to the characters who did the work; a repo diary is a checklist
per repository in four tiers, Easy to Elite. Like experience and
achievements they are read from rows other features already write, never
stored: each step and task is done at the time of the row that first met it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from embry0.services.context_audit import report_counts
from embry0.storage.repositories.progression import JobRow, Journal, RunRow

FINISHED = ("completed", "pr_merged")
DIARY_TIERS: tuple[str, ...] = ("easy", "medium", "hard", "elite")
#: The quiet stretch an Elite diary asks for.
QUIET_DAYS = 30


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


def _quiet_stretch(jobs: Sequence[JobRow]) -> datetime | None:
    """The first finished job a full quiet stretch after the repo's last failure, or after its first job."""
    since: datetime | None = None
    for job in sorted(jobs, key=lambda j: j.at):
        if since is None:
            since = job.created_at
        if job.status == "failed":
            since = job.at
        elif job.status in FINISHED and job.at - since >= timedelta(days=QUIET_DAYS):
            return job.at
    return None


def _diary(repo: str, jobs: Sequence[JobRow], runs: Sequence[RunRow]) -> Diary:
    merges = [j.at for j in jobs if j.status == "pr_merged"]
    qa_runs = [r.at for r in runs if r.agent_type == "qa"]
    qa_issues = {r.issue_id for r in runs if r.agent_type == "qa" and r.issue_id}
    audits = [j for j in jobs if j.template == "context-audit" and j.status == "completed"]
    clean_audits = [j.at for j in audits if report_counts(j.report)[0] == 0]
    passes = [j.at for j in jobs if j.template == "qa" and j.status == "completed"]
    passes += [j.at for j in jobs if j.status == "pr_merged" and j.issue_id in qa_issues]

    easy = DiaryTier(
        "easy",
        [
            Task("Triage an issue", _first(r.at for r in runs if r.agent_type == "triage" and r.issue_id)),
            Task("Finish a job", _first(j.at for j in jobs if j.status in FINISHED)),
            Task("Run QA", _first(qa_runs + [j.at for j in jobs if j.template == "qa" and j.status in FINISHED])),
        ],
    )
    medium = DiaryTier(
        "medium",
        [
            Task("Merge a pull request", _first(merges)),
            Task("Pass QA", _first(passes)),
            Task("Audit its context", _first(j.at for j in audits)),
        ],
    )
    hard = DiaryTier(
        "hard",
        [
            Task("Merge 10 pull requests", _nth(merges, 10)),
            Task("Audit its context and find no outdated claim", _first(clean_audits)),
            Task("Review 10 times", _nth((r.at for r in runs if r.agent_type in ("review", "pr-reviewer")), 10)),
        ],
    )
    below = _when_all([easy.completed_at, medium.completed_at, hard.completed_at])
    elite = DiaryTier(
        "elite",
        [
            Task("Merge 50 pull requests", _nth(merges, 50)),
            Task(f"Go {QUIET_DAYS} days without a failed job", _quiet_stretch(jobs)),
            Task("Complete every task above", below),
        ],
    )
    return Diary(repo, [easy, medium, hard, elite])


def evaluate_diaries(journal: Journal) -> list[Diary]:
    """A diary for every repository with a job, those with the most tiers done first."""
    jobs: dict[str, list[JobRow]] = defaultdict(list)
    runs: dict[str, list[RunRow]] = defaultdict(list)
    for job in journal.jobs:
        jobs[job.repo].append(job)
    for run in journal.runs:
        runs[run.repo].append(run)
    diaries = [_diary(repo, jobs[repo], runs[repo]) for repo in sorted(jobs)]

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


def _first_steps(journal: Journal) -> QuestState:
    by_repo: dict[str, list[JobRow]] = defaultdict(list)
    for job in journal.jobs:
        by_repo[job.repo].append(job)
    candidates = []
    for repo, jobs in by_repo.items():
        onboarded = _first(j.at for j in jobs if j.template == "onboard" and j.status == "completed")
        if onboarded is None:
            continue
        after = [j for j in jobs if j.created_at > onboarded]
        finished = _first(j.at for j in after if j.status in FINISHED)
        merged = _first(j.at for j in after if j.status == "pr_merged")
        candidates.append((repo, [onboarded, finished, merged]))
    where, times = _best(candidates)
    times = times or [None, None, None]
    return QuestState(
        "first_steps",
        "First Steps",
        1,
        "1 quest point; Operations and Coding XP",
        [
            Task("Onboard a repository", times[0]),
            Task("Finish its first job", times[1]),
            Task("Merge its first pull request", times[2]),
        ],
        where=where,
        xp=[Reward("onboarding", "operations", 40_000), Reward("developer", "coding", 40_000)],
    )


def _try_again(journal: Journal) -> QuestState:
    failed: dict[str, datetime] = {}
    for job in sorted(journal.jobs, key=lambda j: j.at):
        if job.issue_id and job.status == "failed":
            failed.setdefault(job.issue_id, job.at)
    comebacks = [
        (job.issue_id, job.at)
        for job in journal.jobs
        if job.issue_id and job.status == "pr_merged" and job.issue_id in failed and failed[job.issue_id] < job.at
    ]
    first = min(comebacks, key=lambda c: c[1]) if comebacks else None
    return QuestState(
        "try_again",
        "Try, Try Again",
        1,
        "1 quest point; Recovery XP",
        [
            Task("Watch a job on an issue fail", failed[first[0]] if first else _first(failed.values())),
            Task("Merge a later job on that issue", first[1] if first else None),
        ],
        where=first[0] if first else None,
        xp=[Reward("developer", "recovery", 60_000)],
    )


def _gatekeeper(journal: Journal) -> QuestState:
    reviewed: dict[str, datetime] = {}
    tested: dict[str, datetime] = {}
    merged: dict[str, datetime] = {}
    for run in sorted(journal.runs, key=lambda r: r.at):
        if run.issue_id and run.agent_type in ("review", "pr-reviewer"):
            reviewed.setdefault(run.issue_id, run.at)
        if run.issue_id and run.agent_type == "qa":
            tested.setdefault(run.issue_id, run.at)
    for job in sorted(journal.jobs, key=lambda j: j.at):
        if job.issue_id and job.status == "pr_merged":
            merged.setdefault(job.issue_id, job.at)
    issues = set(reviewed) | set(tested) | set(merged)
    where, times = _best((i, [reviewed.get(i), tested.get(i), merged.get(i)]) for i in issues)
    times = times or [None, None, None]
    return QuestState(
        "gatekeeper",
        "Gatekeeper",
        2,
        "2 quest points; Reviewing and Quality assurance XP",
        [
            Task("Review an issue's pull request", times[0]),
            Task("Pass QA on it", times[1]),
            Task("Merge it", times[2]),
        ],
        where=where,
        xp=[Reward("review", "reviewing", 50_000), Reward("qa", "quality_assurance", 50_000)],
    )


def _divine_warden(journal: Journal, diaries: Sequence[Diary], total_reached: datetime | None) -> QuestState:
    elite = _first(t for d in diaries if (t := d.tier("elite").completed_at) is not None)
    return QuestState(
        "divine_warden",
        "Divine Warden",
        5,
        "5 quest points; a Divine trim on every cape",
        [
            Task("Merge 100 pull requests", _nth((j.at for j in journal.jobs if j.status == "pr_merged"), 100)),
            Task("Complete a repo diary at Elite", elite),
        ],
        requirement="A character at total level 1,000",
        requirement_met_at=total_reached,
    )


def quest_rewards(journal: Journal) -> list[tuple[datetime, Reward]]:
    """Each XP reward of a completed quest, at its completion: computed before levels, which they feed."""
    out: list[tuple[datetime, Reward]] = []
    for quest in (_first_steps(journal), _try_again(journal), _gatekeeper(journal)):
        if quest.completed_at is not None:
            out.extend((quest.completed_at, reward) for reward in quest.xp)
    return out


def evaluate_quests(journal: Journal, diaries: Sequence[Diary], total_reached: datetime | None) -> list[QuestState]:
    """Every quest's state. ``total_reached`` is when a character first reached total level 1,000."""
    return [
        _first_steps(journal),
        _try_again(journal),
        _gatekeeper(journal),
        _divine_warden(journal, diaries, total_reached),
    ]
