"""The context audit on a schedule (ADR 0020).

A coding agent takes the files that steer it (``CLAUDE.md``, ``AGENTS.md``,
a skill, a rules directory: :mod:`embry0.services.instruction_files`) as
instructions, so a line in one that went stale reaches the agent as a fact.
A repository whose ``context_audit.days`` resolves to a number of days (the
settings document's, its organization's layer and its own, #170; until then
the organization's ``context_audit_days``) has those files audited without
anyone starting a job: one loop in the orchestrator wakes every
``context_audit.check_minutes``, reads the default branch of each such active
registered repository with its organization's credential, and starts a
:data:`TEMPLATE` job for each one that is due (:func:`due_reason`), at most
the owner's cap per wake, each job with the owner's hard budget: the
settings document's ``budget`` section, its
``context_audit_max_jobs_per_tick`` and ``context_audit_job_usd`` as the
owner's layer resolves them. Whether a job opens a pull request from its
patch is the repository's ``context_audit.open_prs``.

The forge is read sparingly. A head read sends the ETag of the last one, so
a branch that did not move answers 304, which GitHub does not count against
the credential's limit, and nothing more is read for that repository. Each
answer's rate headers are kept per credential: once the calls left fall
below ``context_audit.rate_reserve_percent`` of its ``X-RateLimit-Limit``,
the organizations reading with it are deferred, no paid read and no start,
until the window ends, so the jobs that use the same credential keep the
last calls. The window is the reset the forge named, when that lies ahead
by this host's clock (an hour at most); otherwise fifteen minutes, after
which one call asks the forge again (:class:`_Rate`). A forge that sends no
limit is never deferred.

Which files a repository's audit reads is the ``context_audit`` section's
``files`` and ``exclude_dirs`` as the repository resolves them (its
organization's layer and its own over the deployment's), a
:class:`~embry0.services.instruction_files.FileRules`. The schedule selects
and fingerprints the files with it, and the job's task lists them with the
same rules (:func:`audit_task`). A wake reads every setting it uses from one
snapshot of the settings documents, the one the budget was read from
(:class:`TickSettings`), at its top, so a change applies on the next wake with
no restart and a write that lands during a wake never hands it two
versions. A repository's reading of the forge is kept for the next wake
only while its head and its rules both stay the same.

A person can start the same jobs at once (:meth:`ContextAuditLoop.run`,
``POST /orgs/{owner}/context-audit/run``), through the same start, so a job
started by hand is an anchor like any other.

The schedule is anchored on the recorded jobs, never on the process clock,
so a restart neither postpones a due audit nor repeats a recent one. The
newest job counts whatever became of it, a failed one included, and so does
a start refused before any job existed (the template missing, the executor
down): the loop keeps the time of each repository's last refusal. Either way
a repository whose files did not change is tried again one interval later,
not on every tick. The refusals live in the process, so a restart tries such
a repository once more. A finding's fix job, which an owner starts from the
findings feed, is no audit and anchors nothing, though it holds its
repository while it runs (:data:`FIX_ACTOR`).

The budget gate is asked before each start, as a label trigger asks it
(#284). The schedule's actor skips the per-author hourly limit
(``dispatch_actors``); while the gate refuses (a daily or monthly budget),
no job row is written and nothing counts as an attempt: a tick ends
there and the repositories it did not reach are due again on the next. A job
the gate refused anyway, between the ask and the run, did no work and is no
anchor either: the jobs table's newest-job read leaves it out.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal, NamedTuple, Protocol

import structlog

from embry0.audit.helpers import emit_audit
from embry0.orchestration import dispatch_actors
from embry0.services.context_audit_command import with_command
from embry0.services.instruction_files import FileRules
from embry0.services.loop_schedule import seconds_until_next_run
from embry0.services.organizations import Organization
from embry0.services.run_with import apply_run_with
from embry0.settings.sections.context_audit import (
    CHECK_MINUTES_MIN,
    CONTEXT_AUDIT,
    ContextAuditSettings,
    audit_days,
    file_rules,
)
from embry0.storage.repositories import audit_jobs
from embry0.storage.schemas import FINISHED_WELL_STATUSES

if TYPE_CHECKING:
    from embry0.settings.sections.budget import BudgetSettings

logger = structlog.get_logger(__name__)

#: The pipeline template every scheduled job names.
TEMPLATE = "context-audit"

#: The actor of every job the schedule starts, in ``jobs.dispatched_by`` and
#: in the job's audit row.
ACTOR = dispatch_actors.CONTEXT_AUDIT

#: The actor of a finding's fix job: the job an owner starts by accepting one
#: row of an audit's findings (owner proposals, spec 2026-10-24). It runs on
#: :data:`TEMPLATE`, but it fixes one claim and is no audit: it is never the
#: due rule's anchor or the survey's newest job, and the findings feed reads
#: past it. While it runs it still holds its repository, as every
#: context-audit job does, so no audit opens a pull request on the same
#: files beside it. The actor is the marker because ``jobs.dispatched_by`` is
#: written once, in the reserved ``embry0:`` names, while plan_route may
#: rewrite the template column.
FIX_ACTOR = audit_jobs.FIX_ACTOR
#: The agent an audit is led by: what makes a job an audit for this loop, progression and the findings feed alike
#: (``embry0.storage.repositories.audit_jobs``, #167).
AUDITOR = audit_jobs.AUDITOR

#: Files the task names one by one; past it the task says how many more.
TASK_FILE_LIMIT = 200

#: The longest the loop sleeps before it reads ``check_minutes`` again while
#: it waits: the key's least value, so the default wait is one sleep, and a
#: shorter interval written during a longer wait applies within it.
RECHECK_SECONDS = CHECK_MINUTES_MIN * 60

# Why a repository is due, or why not.
#: A context-audit job of the repository is pending, running, awaiting input
#: or paused: never due then.
LIVE = "live"
#: No context-audit job yet.
FIRST = "first"
#: The instruction files differ from those the newest audit recorded.
CHANGED = "changed"
#: The newest audit is an interval old and the branch moved since.
AGED = "aged"
#: None of these.
CURRENT = "current"
#: The organization's setting is off.
OFF = "off"
DUE_REASONS = frozenset({FIRST, CHANGED, AGED})

# Why a start by hand passed a repository over, besides LIVE and CURRENT, and
# the reason it gives a job it started although the due rule said current.
#: Past the run's limit.
LIMIT = "limit"
#: The start was refused before a job existed: no template, no executor.
REFUSED = "refused"
#: The budget gate refused the start, or one before it in the same run: no
#: job was written and nothing counts as an attempt.
GATED = "gated"
#: The forge did not answer for the newest commit or the tree.
UNREAD = "unread"
#: The default branch holds no instruction file, or no commit.
NO_FILES = "no_instruction_files"
#: Started with ``force`` while the due rule said current.
FORCED = "forced"
#: Not read, or read for free and not started: its organization's credential is down to
#: ``context_audit.rate_reserve_percent`` of its limit.
DEFERRED = "deferred"
_RESERVE_DETAIL = "its organization's credential is down to context_audit.rate_reserve_percent of its rate limit"


@dataclass(frozen=True)
class RepoContext:
    """A repository's instruction files at the head of its default branch, and the rules that selected them.

    ``rules`` are the ``context_audit`` section's files as the repository
    resolved them when it was read; its job's task lists the files with the
    same rules (:func:`audit_task`).
    """

    repo: str
    head_sha: str
    files: tuple[str, ...]
    fingerprint: str
    #: Required, by keyword: a builder that left it out would hand the agent the built-in list.
    rules: FileRules = field(kw_only=True)


@dataclass(frozen=True)
class Attempt:
    """The newest context audit of a repository, as the due rule reads it."""

    started_at: datetime
    #: When it finished its work (:data:`~embry0.storage.schemas.FINISHED_WELL_STATUSES`: completed,
    #: its pull request merged or closed since); None for any other outcome, which counts from its start.
    finished_at: datetime | None = None
    fingerprint: str | None = None
    head_sha: str | None = None

    @classmethod
    def from_job(cls, row: Mapping[str, Any]) -> Attempt | None:
        """The attempt a job row records, or None for a row with no creation time."""
        started = _when(row.get("created_at"))
        if started is None:
            return None
        config = row.get("pipeline_config")
        config = config if isinstance(config, Mapping) else {}
        recorded = {key: value if isinstance(value, str) and value else None for key, value in config.items()}
        return cls(
            started_at=started,
            finished_at=_when(row.get("finished_at")) if row.get("status") in FINISHED_WELL_STATUSES else None,
            fingerprint=recorded.get("context_fingerprint"),
            head_sha=recorded.get("head_sha"),
        )


def _when(value: Any) -> datetime | None:
    """A timestamp as an aware datetime: fakes and JSON answer ISO strings."""
    if isinstance(value, str) and value:
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: Any) -> str | None:
    when = _when(value)
    return when.isoformat() if when else None


def fingerprint(blobs: Mapping[str, str]) -> str:
    """sha256 over the sorted ``path<TAB>blob sha`` lines of the instruction files.

    A blob sha is git's hash of a file's content, so the fingerprint changes
    when an instruction file is added, removed, renamed or edited, and only
    then.
    """
    text = "\n".join(f"{path}\t{sha}" for path, sha in sorted(blobs.items()))
    return hashlib.sha256(text.encode()).hexdigest()


# The two lines the context-auditor's report starts with; the command its task
# carries fixes them (``context_audit_command.command``: "Programs read the
# counts from the start of your report").
_STALE_LINE = re.compile(r"^Stale:[ \t]*(\d+)[ \t]*$", re.MULTILINE)
_FILES_LINE = re.compile(r"^Files:[ \t]*(\d+)[ \t]*$", re.MULTILINE)


def report_counts(report: str | None) -> tuple[int | None, int | None]:
    """The counts an audit's report starts with: its stale findings and the instruction files it read.

    Each is None when the report does not carry it, such as a report cut
    short. The job's report can hold the agent's own lines before them, so
    each is the first line of its form, wherever it stands.
    """
    if not report:
        return None, None
    stale = _STALE_LINE.search(report)
    files = _FILES_LINE.search(report)
    return (int(stale.group(1)) if stale else None, int(files.group(1)) if files else None)


def due_reason(
    newest: Attempt | None,
    current: RepoContext,
    *,
    days: int | None,
    now: datetime,
    live: bool = False,
    by_hand: bool = False,
) -> str:
    """Why a repository whose newest context audit is ``newest`` is due at ``now``, or why not.

    In this order: :data:`OFF` when ``days`` is None; :data:`LIVE` while a
    context-audit job of it has not finished; :data:`FIRST` with no audit
    yet; :data:`CHANGED` when the files differ from those the newest audit
    recorded (one that recorded none, such as a job started without the
    schedule's start, is not compared); :data:`AGED` when the newest audit
    is ``days`` old and the branch head moved since; else :data:`CURRENT`.
    The age is read as
    :func:`~embry0.services.loop_schedule.seconds_until_next_run` reads its
    anchors: an audit that finished its work (completed, its pull request
    merged or closed since) from when it finished, any other (failed,
    cancelled, refused before it existed) from when it started, so a failure
    waits a full interval like a success. A job the budget gate refused is
    never ``newest``: it did no work.

    ``by_hand`` is the rule a start by hand reads: the setting being off is
    not :data:`OFF`, and with no interval nothing is :data:`AGED`, so a
    repository whose files the newest audit recorded is current however old
    that audit is.
    """
    if days is None and not by_hand:
        return OFF
    if live:
        return LIVE
    if newest is None:
        return FIRST
    if newest.fingerprint is not None and newest.fingerprint != current.fingerprint:
        return CHANGED
    if days is None:
        return CURRENT
    wait = seconds_until_next_run(
        now=now,
        interval_seconds=days * 86400,
        min_delay_seconds=0,
        last_run_at=newest.finished_at,
        last_attempt_at=newest.started_at,
    )
    if wait <= 0 and newest.head_sha != current.head_sha:
        return AGED
    return CURRENT


def _newer(job: Attempt | None, refused: Attempt | None) -> Attempt | None:
    if job is None or refused is None:
        return job or refused
    return refused if refused.started_at > job.started_at else job


# ---- reading the forge ----


class UnsupportedForge(ValueError):
    """The organization's forge is not one whose trees the audit reads."""


class UnknownRepository(LookupError):
    """A start by hand named a repository that is not an active registered one of the organization."""


class InvalidRun(ValueError):
    """A start by hand asked for something a run does not do."""


class ForgeReadError(RuntimeError):
    """The forge did not answer a read the way the audit needs."""


class GateRefused(RuntimeError):
    """The budget gate refused a start before a job existed; it is not an attempt."""

    def __init__(self, limit: str, message: str) -> None:
        super().__init__(message or limit or "refused by the budget gate")
        #: Which layer refused: ``rate_limit``, ``daily`` or ``monthly``.
        self.limit = limit
        #: The gate's operator-facing detail.
        self.message = message


class _Forge(Protocol):
    async def get(self, path: str, **kwargs: Any) -> Any: ...


def _json(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is "nothing"
        return None


def _header(response: Any, name: str) -> str | None:
    """A response header by its lowercase ``name``, from httpx's headers or a plain mapping; None without it."""
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get(name)
    if value is None and isinstance(headers, Mapping):
        value = next((v for k, v in headers.items() if str(k).lower() == name), None)
    return None if value is None else str(value)


@dataclass(frozen=True)
class Head:
    """The head of a default branch as one read found it, and the ETag the forge answered it with."""

    sha: str
    tree: str
    etag: str | None = None


#: What a head read answers when the forge said 304: the branch is where the read whose ETag it sent found it.
NotModified = Literal["not_modified"]
NOT_MODIFIED: NotModified = "not_modified"


async def read_head(client: _Forge, repo: str) -> tuple[str, str] | None:
    """The head commit sha and tree sha of the default branch; None for an empty repository.

    The commit listing starts at the default branch when no ref is given, so
    its first entry is that branch's head. No ``repo`` is passed to the
    client, so it acts with the token it was built with, the organization's.
    """
    head = await _read_head(client, repo)
    return None if head is None or head == NOT_MODIFIED else (head.sha, head.tree)


async def _read_head(client: _Forge, repo: str, *, etag: str | None = None) -> Head | NotModified | None:
    """:func:`read_head`, sending ``etag`` as ``If-None-Match``: :data:`NOT_MODIFIED` when the forge answers 304."""
    kwargs: dict[str, Any] = {"params": {"per_page": 1}}
    if etag:
        kwargs["extra_headers"] = {"If-None-Match": etag}
    resp = await client.get(f"/repos/{repo}/commits", **kwargs)
    if resp.status_code == 304 and etag:
        return NOT_MODIFIED
    if resp.status_code == 409:  # GitHub's answer for a repository with no commit
        return None
    body = _json(resp) if resp.status_code == 200 else None
    if not isinstance(body, list):
        raise ForgeReadError(f"reading the newest commit answered {resp.status_code}")
    if not body:
        return None
    commit = body[0] if isinstance(body[0], dict) else {}
    sha = commit.get("sha")
    tree = ((commit.get("commit") or {}).get("tree") or {}).get("sha")
    if not (isinstance(sha, str) and sha and isinstance(tree, str) and tree):
        raise ForgeReadError("the newest commit carries no sha or tree")
    return Head(sha, tree, _header(resp, "etag"))


# ---- the forge's rate limit ----


class ReserveReached(RuntimeError):
    """A credential's calls left are below ``context_audit.rate_reserve_percent`` of its limit: the call is not made."""


#: How long a credential found low waits before one call asks the forge again, when the forge named no usable
#: reset: none, not a number, or one already past by this host's clock (the two clocks disagree).
LOW_WAIT: Final = timedelta(minutes=15)
#: The longest a credential found low waits for the reset the forge names: GitHub's window is an hour.
MAX_LOW_WAIT: Final = timedelta(hours=1)


@dataclass
class _Rate:
    """What the forge last said of one credential's rate limit, and when this host heard it."""

    remaining: int | None = None
    limit: int | None = None
    #: The reset the last answer named; None when it named none, or not as a number.
    reset: datetime | None = None
    #: When that answer came, by this host's clock.
    seen: datetime | None = None
    #: The end of the low window last warned about: no other warning until it is over.
    noted: datetime | None = None
    #: The end of the window whose probe was logged: one line per window.
    probed: datetime | None = None
    #: Set while a probe is out: every other call waits for its answer, then looks at the state again.
    probe: asyncio.Event | None = field(default=None, repr=False, compare=False)

    def below(self, percent: int) -> bool:
        """Whether the calls left are below ``percent`` of the limit; never when the forge sent no limit."""
        if self.remaining is None or self.limit is None or percent <= 0:
            return False
        return self.remaining * 100 < self.limit * percent

    def until(self) -> datetime | None:
        """When the low window the last answer opened ends: the reset it named, when that lies after the answer
        by this host's clock, at most :data:`MAX_LOW_WAIT` on; else :data:`LOW_WAIT` after the answer."""
        if self.seen is None:
            return None
        if self.reset is not None and self.reset > self.seen:
            return min(self.reset, self.seen + MAX_LOW_WAIT)
        return self.seen + LOW_WAIT

    def low(self, percent: int, now: datetime) -> bool:
        """Whether the calls left are below the share and the window that said so is still open."""
        until = self.until()
        return self.below(percent) and until is not None and now < until

    def stale(self, percent: int, now: datetime) -> bool:
        """Below the share, but its window is over: one call, the probe, may ask the forge again."""
        return self.below(percent) and not self.low(percent, now)

    def update(self, response: Any, now: datetime) -> None:
        """Keep an answer's rate headers; an answer that sends no ``X-RateLimit-Remaining`` changes nothing."""
        remaining = _header(response, "x-ratelimit-remaining")
        if remaining is None or not remaining.isdigit():
            return
        limit, reset = _header(response, "x-ratelimit-limit"), _header(response, "x-ratelimit-reset")
        self.remaining = int(remaining)
        self.limit = int(limit) if limit is not None and limit.isdigit() else None
        self.reset = datetime.fromtimestamp(int(reset), UTC) if reset is not None and reset.isdigit() else None
        self.seen = now


def _conditional(kwargs: Mapping[str, Any]) -> bool:
    """Whether a call sends an ETag: a head read GitHub answers 304, at no cost, when the branch did not move."""
    return bool((kwargs.get("extra_headers") or {}).get("If-None-Match"))


class _Reserved:
    """A forge client that keeps each answer's rate headers in ``rate`` and spends no call the share keeps.

    While the credential is low, a head read with an ETag still goes out:
    GitHub answers it 304 at no cost when the branch did not move, and the
    answer's headers keep the state current for nothing. The first such read
    the forge answers otherwise, a branch that moved, closes the client for
    the rest of the survey, so a low credential pays at most one call per
    survey. Every other call raises :class:`ReserveReached`. Once the low
    window is over with the calls left still below the share, one call, the
    probe, asks the forge again; every other call waits for its answer and
    then goes out, or not, by what that answer said.
    """

    def __init__(
        self, client: _Forge, rate: _Rate, percent: int, clock: Callable[[], datetime], owner: str = ""
    ) -> None:
        self._client, self._rate, self._percent, self._clock, self._owner = client, rate, percent, clock, owner
        self._paid_while_low = False

    async def _call(self, path: str, kwargs: Mapping[str, Any]) -> Any:
        response = await self._client.get(path, **kwargs)
        self._rate.update(response, self._clock())
        return response

    async def get(self, path: str, **kwargs: Any) -> Any:
        rate = self._rate
        while True:
            now = self._clock()
            if rate.low(self._percent, now):
                if self._paid_while_low or not _conditional(kwargs):
                    raise ReserveReached(path)
                response = await self._call(path, kwargs)
                if response.status_code != 304:
                    self._paid_while_low = True
                return response
            if not rate.stale(self._percent, now):
                return await self._call(path, kwargs)
            if rate.probe is not None:
                await rate.probe.wait()
                continue
            window = rate.until()
            if rate.probed != window:
                rate.probed = window
                logger.info(
                    "context_audit_rate_reserve_probe",
                    owner=self._owner,
                    remaining=rate.remaining,
                    limit=rate.limit,
                    hint="the low window is over: one call asks the forge for the calls left",
                )
            rate.probe = probe = asyncio.Event()
            try:
                return await self._call(path, kwargs)
            finally:
                rate.probe = None
                probe.set()


async def read_blobs(client: _Forge, repo: str, tree_sha: str) -> dict[str, str]:
    """Every file in the tree, path to blob sha, read recursively in one request.

    A tree the forge truncated (GitHub stops at 100,000 entries) is refused:
    a fingerprint over part of the files would change when the cut moves.
    """
    resp = await client.get(f"/repos/{repo}/git/trees/{tree_sha}", params={"recursive": "1"})
    body = _json(resp) if resp.status_code == 200 else None
    if not isinstance(body, dict) or not isinstance(body.get("tree"), list):
        raise ForgeReadError(f"reading the tree answered {resp.status_code}")
    if body.get("truncated"):
        raise ForgeReadError("the tree is larger than one read returns")
    return {
        entry["path"]: entry["sha"]
        for entry in body["tree"]
        if isinstance(entry, dict)
        and entry.get("type") == "blob"
        and isinstance(entry.get("path"), str)
        and isinstance(entry.get("sha"), str)
    }


# ---- what the read endpoint and the loop see ----


@dataclass(frozen=True)
class RepoState:
    """One repository with instruction files, its newest audit and whether it is due."""

    context: RepoContext
    newest_job: Mapping[str, Any] | None
    refused_at: datetime | None
    reason: str
    #: The counts the newest job's report starts with (:func:`report_counts`),
    #: when it finished its work and they were asked for.
    report: tuple[int | None, int | None] | None = None
    #: Days between its scheduled audits, as it resolves ``context_audit.days``; None while its schedule is off.
    days: int | None = None

    @property
    def due(self) -> bool:
        return self.reason in DUE_REASONS

    def to_dict(self) -> dict[str, Any]:
        job = self.newest_job
        newest = None
        if job is not None:
            stale, files_read = self.report or (None, None)
            cost = job.get("total_cost_usd")
            newest = {
                "job_id": job.get("job_id"),
                "status": job.get("status"),
                "created_at": _iso(job.get("created_at")),
                "finished_at": _iso(job.get("finished_at")),
                "total_cost_usd": float(cost) if isinstance(cost, int | float) else None,
                "error_code": job.get("error_code"),
                "pr_url": job.get("pr_url"),
                "stale": stale,
                "files_read": files_read,
            }
        return {
            "repo": self.context.repo,
            "files": len(self.context.files),
            "fingerprint": self.context.fingerprint,
            "head_sha": self.context.head_sha,
            "newest_job": newest,
            "refused_at": _iso(self.refused_at),
            "due": self.due,
            "reason": self.reason,
            "days": self.days,
        }


@dataclass(frozen=True)
class Survey:
    """An organization's repositories as the schedule reads them."""

    owner: str
    days: int | None
    repos: tuple[RepoState, ...]
    #: Repositories whose head or tree could not be read, with why.
    unread: tuple[tuple[str, str], ...] = ()
    #: When the oldest forge reading in it was taken; None with no repository.
    read_at: datetime | None = None
    #: Repositories not read because the organization's credential is down to its rate reserve. Each one's last
    #: reading, when it has one taken with the rules it reads now, is still among ``repos``.
    deferred: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "context_audit_days": self.days,
            "read_at": _iso(self.read_at),
            "template": TEMPLATE,
            "repos": [state.to_dict() for state in self.repos],
            "unread": [{"repo": repo, "detail": detail} for repo, detail in self.unread],
            "deferred": list(self.deferred),
        }


@dataclass(frozen=True)
class Started:
    """A job a start by hand started, and why the repository was due (or :data:`FORCED`)."""

    repo: str
    job_id: str
    reason: str


@dataclass(frozen=True)
class Skipped:
    """A repository a start by hand passed over, and why."""

    repo: str
    reason: str
    detail: str | None = None


@dataclass(frozen=True)
class RunResult:
    """What one start by hand started and passed over (:meth:`ContextAuditLoop.run`)."""

    owner: str
    days: int | None
    repo: str | None
    force: bool
    #: The most it would start without ``repo``; None with one.
    limit: int | None
    budget_usd: float
    started: tuple[Started, ...]
    skipped: tuple[Skipped, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "context_audit_days": self.days,
            "repo": self.repo,
            "force": self.force,
            "limit": self.limit,
            "budget_usd": self.budget_usd,
            "started": [{"repo": s.repo, "job_id": s.job_id, "reason": s.reason} for s in self.started],
            "skipped": [{"repo": s.repo, "reason": s.reason, "detail": s.detail} for s in self.skipped],
        }


class _Organizations(Protocol):
    async def list_all(self, *, include_removed: bool = False) -> list[Organization]: ...


class _Registry(Protocol):
    async def list_all(self, include_inactive: bool = False) -> list[dict[str, Any]]: ...


class _Jobs(Protocol):
    async def newest_per_repo(
        self,
        repos: Collection[str],
        *,
        pipeline_template: str | None = None,
        dispatched_by: str | None = None,
        exclude_dispatched_by: Collection[str] = (),
        leader_agent: str | None = None,
    ) -> dict[str, dict[str, Any]]: ...

    async def result_summaries(self, job_ids: Collection[str]) -> dict[str, str | None]: ...


class _Reading(NamedTuple):
    """One repository's last forge reading."""

    at: datetime
    #: None for a repository with no commit, or one the forge did not answer for.
    context: RepoContext | None
    #: Why the forge did not answer, when it did not.
    unread: str | None
    #: The rules the reading selected its files with: a survey with other rules reads the forge again.
    rules: FileRules


#: The client that reads an organization's repositories with its credential.
ClientFor = Callable[[Organization], Awaitable[_Forge]]

#: Starts one context-audit job with that hard cap in USD, opening a pull
#: request from its patch when the third argument is true, and returns its id.
StartJob = Callable[[RepoContext, float, bool], Awaitable[str]]


class _Refusal(Protocol):
    limit: str
    message: str


#: Asks the budget gate whether a job of :data:`ACTOR` may start now: its
#: refusal (a :class:`~embry0.orchestration.budget_gate.DispatchDecision`), or None.
AskGate = Callable[[], Awaitable[_Refusal | None]]


class _BudgetView(Protocol):
    #: The snapshot of the settings documents the budget was read from; None when none could be read.
    @property
    def snapshot(self) -> Any: ...

    def organization(self, owner: str, settings: Mapping[str, Any] | None = None) -> BudgetSettings: ...

    def moved_values(self, section: str, owner: str, settings: Mapping[str, Any]) -> dict[str, Any]: ...


#: The ``budget`` section at one moment, read for these organizations
#: (:meth:`embry0.services.budget_policy.BudgetPolicy.view`): each one's caps,
#: its own settings read for a moved key its layer has not imported yet, and
#: the snapshot it was read from, which the ``context_audit`` section is read
#: from too (:class:`TickSettings`).
BudgetReader = Callable[[Sequence[Organization]], Awaitable[_BudgetView]]


class SettingsUnreadable(RuntimeError):
    """No snapshot of the settings documents could be read, so which files each repository reads is unknown.

    Nothing is read or started then: the built-in files in place of an
    organization's own would make each of its repositories due at once.
    """


@dataclass(frozen=True)
class TickSettings:
    """What one wake, one survey or one run by hand reads, all from one snapshot of the settings documents."""

    #: The ``budget`` section's caps, as each organization reads them, and the settings an organization whose
    #: layer has not imported them yet still reads from its own row (``BudgetView.moved_values``).
    budget: _BudgetView
    #: The snapshot both are read from, which the ``context_audit`` section is read from too.
    snapshot: Any

    def resolved(self, org: Organization, repo: str | None = None) -> ContextAuditSettings:
        """The ``context_audit`` section as ``repo`` reads it (its organization's layer and its own over the
        deployment's), or as ``org`` does without one.

        Until the organization's layer has imported ``days`` and ``open_prs``
        from its row (a first start whose copy failed), it reads them from the
        row as the release before did, where its layer does not set them; a
        repository's own layer still wins.
        """
        found = (
            self.snapshot.resolve(CONTEXT_AUDIT, repo=repo)
            if repo
            else self.snapshot.resolve(CONTEXT_AUDIT, owner=org.owner)
        )
        model: ContextAuditSettings = found.model
        legacy = self.budget.moved_values(CONTEXT_AUDIT.name, org.owner, org.settings)
        if not legacy:
            return model
        repository = {
            key for scope, values in found.applied.items() if scope.startswith("repository:") for key in values
        }
        updates = {key: value for key, value in legacy.items() if key not in repository}
        return model.model_copy(update=updates) if updates else model

    def rules(self, org: Organization, repo: str) -> FileRules:
        """The files ``repo`` reads: ``files`` and ``exclude_dirs`` as its organization's layer and its own resolve
        them over the deployment's."""
        return file_rules(self.resolved(org, repo))

    def days(self, org: Organization, repo: str | None = None) -> int | None:
        """Days between ``repo``'s scheduled audits, or ``org``'s without one; None while the schedule is off."""
        return audit_days(self.resolved(org, repo))

    def open_prs(self, org: Organization, repo: str) -> bool:
        """Whether an audit of ``repo`` opens a pull request from the patch it checked."""
        return self.resolved(org, repo).open_prs

    @property
    def deployment(self) -> ContextAuditSettings:
        """The deployment's values: the keys only it may set."""
        model: ContextAuditSettings = self.snapshot.get(CONTEXT_AUDIT)
        return model

    @property
    def interval_seconds(self) -> int:
        """Seconds between two wakes: the deployment's ``check_minutes``."""
        return self.deployment.check_minutes * 60

    @property
    def read_concurrency(self) -> int:
        """Repositories of one organization read from the forge at once: the deployment's ``read_concurrency``."""
        return self.deployment.read_concurrency

    @property
    def rate_reserve_percent(self) -> int:
        """The share of a credential's rate limit below which the schedule defers: ``rate_reserve_percent``."""
        return self.deployment.rate_reserve_percent


def _detail(exc: Exception) -> str:
    """What a refusal says: an HTTPException's detail, else the exception's text, else its type."""
    text = str(getattr(exc, "detail", "") or "") or str(exc)
    return (text or type(exc).__name__)[:200]


def _caps(policy: BudgetSettings) -> tuple[int, float]:
    """The most context-audit jobs one tick starts for an organization, and each one's hard cap in USD."""
    return policy.context_audit_max_jobs_per_tick, policy.context_audit_job_usd


def _owned(org: Organization, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """The registered repositories of ``rows`` under ``org``, on its forge."""
    return [
        str(row["repo"])
        for row in rows
        if str(row["repo"]).split("/", 1)[0].lower() == org.owner and row.get("forge") == org.forge
    ]


def _unsupported(org: Organization) -> str:
    return f"the context audit reads GitHub repositories in this release, not {org.forge}"


class ContextAuditLoop:
    """Starts the scheduled context audits: :meth:`tick` is one pass, :meth:`start` the loop."""

    def __init__(
        self,
        *,
        organizations: _Organizations,
        registry: _Registry,
        jobs: _Jobs,
        client_for: ClientFor,
        start_job: StartJob,
        ask_gate: AskGate,
        budget: BudgetReader,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._organizations = organizations
        self._registry = registry
        self._jobs = jobs
        self._client_for = client_for
        self._start_job = start_job
        # Required, not optional: without it every start past a spend cap
        # becomes a failed job that anchors its repository for an interval.
        self._ask_gate = ask_gate
        # The caps, read from the settings document at the top of each tick
        # and each run, as each owner's layer resolves them, and the snapshot
        # they were read from, which the context_audit section is read from.
        self._budget = budget
        self._clock = clock
        # The interval the last read of the section said, for a wait whose
        # read fails: the default until one succeeds.
        self._interval = CONTEXT_AUDIT.defaults().check_minutes * 60
        # Each repository's last start refused before a job row existed, the
        # one attempt nothing else records.
        self._refused: dict[str, Attempt] = {}
        # The last reading of each repository: a head that has not moved has
        # the same tree, so it is not read again while the rules that
        # selected its files are the same.
        self._contexts: dict[str, RepoContext] = {}
        # Each repository's last head and its ETag, sent with the next head
        # read: a 304 says the branch has not moved, and costs no rate limit.
        self._heads: dict[str, Head] = {}
        # What the forge last said of each credential's rate limit.
        self._rates: dict[str, _Rate] = {}
        # Each repository's last reading and when it was taken, for a survey
        # that asks for no fresh one.
        self._last_read: dict[str, _Reading] = {}
        # What was last logged about each owner skipped, and about each
        # repository of an owner its last read could not read, so a standing
        # problem is one log line, not one per tick.
        self._noted: dict[str, str] = {}
        self._unread: dict[str, dict[str, str]] = {}
        # Held around each start's last look at the jobs and the start itself:
        # the tick and a start by hand run in this one process.
        self._starting = asyncio.Lock()

    async def settings(self, organizations: Sequence[Organization] = ()) -> TickSettings:
        """Every setting a wake reads, now, from one snapshot of the settings documents: the budget as
        ``organizations`` read it, and the ``context_audit`` section from the snapshot it was read from.

        Raises :class:`SettingsUnreadable` when no snapshot could be read.
        """
        view = await self._budget(organizations)
        snapshot = view.snapshot
        if snapshot is None:
            raise SettingsUnreadable("no snapshot of the settings documents could be read")
        return TickSettings(budget=view, snapshot=snapshot)

    async def _read(self, client: _Forge, repo: str, rules: FileRules) -> RepoContext | None:
        known = self._heads.get(repo)
        found = await _read_head(client, repo, etag=known.etag if known is not None else None)
        if found is None:
            self._heads.pop(repo, None)
            return None
        # A 304 is the head the ETag was sent for, so no call is made unless the rules changed since its tree was read.
        head = known if found == NOT_MODIFIED else found
        assert isinstance(head, Head)
        self._heads[repo] = head
        head_sha, tree_sha = head.sha, head.tree
        cached = self._contexts.get(repo)
        if cached is not None and cached.head_sha == head_sha and cached.rules == rules:
            return cached
        blobs = await read_blobs(client, repo, tree_sha)
        files = rules.select(blobs)
        context = RepoContext(
            repo=repo,
            head_sha=head_sha,
            files=tuple(files),
            fingerprint=fingerprint({path: blobs[path] for path in files}),
            rules=rules,
        )
        self._contexts[repo] = context
        return context

    async def _registered(self, org: Organization) -> list[str]:
        """The active registered repositories under ``org``, on its forge."""
        if org.forge != "github":
            raise UnsupportedForge(_unsupported(org))
        return _owned(org, await self._registry.list_all())

    async def _audits(self, repos: Collection[str]) -> tuple[dict[str, dict[str, Any]], set[str]]:
        """Each repository's newest context audit, and the repositories with a context-audit job live.

        Every audit counts: a job the context auditor led (``audit_jobs``,
        #167), as progression and the findings feed count them, a copy of
        the template's included. So does every job of this loop's own
        template, and every job it dispatched, whatever led them: such a job
        works on the instruction files an audit opens a pull request on, and
        an operator who gave the template another leader must not get an
        audit started again on every tick. A finding's fix job
        (:data:`FIX_ACTOR`) is no audit, so it is never the newest and never
        anchors the due rule. While it runs it still holds its repository,
        as every such job does: an audit started beside it could open a pull
        request on the same instruction files. So the live jobs are read
        with it, the newest without.
        """
        every = await self._jobs.newest_per_repo(
            repos, pipeline_template=TEMPLATE, dispatched_by=ACTOR, leader_agent=AUDITOR
        )
        audits = await self._jobs.newest_per_repo(
            repos,
            pipeline_template=TEMPLATE,
            dispatched_by=ACTOR,
            leader_agent=AUDITOR,
            exclude_dispatched_by=(FIX_ACTOR,),
        )
        return audits, {repo for repo, row in every.items() if row.get("live")}

    async def survey(
        self,
        org: Organization,
        *,
        now: datetime | None = None,
        refresh: bool = True,
        reports: bool = False,
        hidden: Callable[[str], bool] | None = None,
        settings: TickSettings | None = None,
    ) -> Survey:
        """Every active registered repository of ``org`` with instruction files, and whether each is due.

        Reads the forge and the jobs table and starts nothing; the read
        endpoint answers with it. A repository with no instruction files, or
        no commit, is left out, and one whose reads failed is listed in
        ``unread``. With ``refresh`` false, each repository's last reading is
        used when there is one, so only the jobs are read (the forge, one
        repository after another, takes seconds per dozen) and ``read_at``
        says how old the oldest reading is. With ``reports``, each newest job
        that finished its work (completed, its pull request merged or closed
        since) carries the counts its report starts with
        (:func:`report_counts`). Raises :class:`UnsupportedForge` for an
        owner on a forge whose trees are not read this way, and what
        ``client_for`` raises when the owner's credential cannot be resolved
        and the forge has to be read.

        ``hidden`` names the repositories the reader may not read: each is
        left out of the survey, neither read from the forge nor listed.

        Each repository's files are those its rules select (:meth:`TickSettings.rules`),
        from ``settings``, a tick's, else read now; a reading taken with other
        rules is never used, ``refresh`` or not. Raises :class:`SettingsUnreadable`
        when no snapshot of the settings documents can be read.
        """
        repos = [repo for repo in await self._registered(org) if hidden is None or not hidden(repo)]
        if settings is None:
            settings = await self.settings([org])
        survey = await self._survey(org, repos, now=now or self._clock(), settings=settings, refresh=refresh)
        return await self._with_reports(survey) if reports else survey

    async def _with_reports(self, survey: Survey) -> Survey:
        """``survey`` with the counts of the report of each newest job that finished its work."""
        finished = {
            str(state.newest_job["job_id"])
            for state in survey.repos
            if state.newest_job is not None and state.newest_job.get("status") in FINISHED_WELL_STATUSES
        }
        if not finished:
            return survey
        summaries = await self._jobs.result_summaries(finished)
        repos = tuple(
            replace(state, report=report_counts(summaries.get(str(state.newest_job["job_id"]))))
            if state.newest_job is not None and str(state.newest_job["job_id"]) in finished
            else state
            for state in survey.repos
        )
        return replace(survey, repos=repos)

    async def _survey(
        self,
        org: Organization,
        repos: list[str],
        *,
        now: datetime,
        settings: TickSettings,
        by_hand: bool = False,
        refresh: bool = True,
    ) -> Survey:
        """:meth:`survey` over ``repos``, each due by :func:`due_reason` with ``by_hand``, read with ``settings``.

        Each repository is due by its own ``days`` and read with its own rules.
        While the organization's credential is down to the rate reserve, a
        repository not read yet in this survey is not read: it is
        ``deferred``, and its last reading is used if it was taken with the
        rules it reads now.
        """
        days = settings.days(org)
        if not repos:
            return Survey(org.owner, days, ())
        newest, live_repos = await self._audits(repos)
        rules = {repo: settings.rules(org, repo) for repo in repos}
        stale = [
            repo
            for repo in repos
            if refresh or repo not in self._last_read or self._last_read[repo].rules != rules[repo]
        ]
        deferred: list[str] = []
        if stale:
            rate = self._rate(org)
            percent = settings.rate_reserve_percent
            known = [repo for repo in stale if (head := self._heads.get(repo)) is not None and head.etag]
            if rate.low(percent, now) and not known:
                # Not one call, the credential resolved or not: none of these can answer 304 for free.
                deferred = list(stale)
            else:
                client = _Reserved(await self._client_for(org), rate, percent, self._clock, org.owner)
                slots = asyncio.Semaphore(settings.read_concurrency)

                async def read(repo: str) -> _Reading | None:
                    async with slots:
                        try:
                            return _Reading(now, await self._read(client, repo, rules[repo]), None, rules[repo])
                        except ReserveReached:
                            return None
                        except Exception as exc:  # noqa: BLE001 - one repository's failure is its own entry
                            detail = str(exc) if isinstance(exc, ForgeReadError) else type(exc).__name__
                            return _Reading(now, None, detail, rules[repo])

                for repo, fresh in zip(stale, await asyncio.gather(*(read(repo) for repo in stale)), strict=True):
                    if fresh is None:
                        deferred.append(repo)
                    else:
                        self._last_read[repo] = fresh
            if deferred or rate.low(percent, now):
                self._note_reserve(org, rate, percent, now)
        states: list[RepoState] = []
        unread: list[tuple[str, str]] = []
        read_at: list[datetime] = []
        for repo in repos:
            reading = self._last_read.get(repo)
            if reading is None or reading.rules != rules[repo]:
                continue  # deferred before it was ever read with these rules
            read_at.append(reading.at)
            if reading.unread is not None:
                unread.append((repo, reading.unread))
                continue
            context = reading.context
            if context is None or not context.files:
                continue
            job = newest.get(repo)
            refused = self._refused.get(repo)
            attempt = _newer(Attempt.from_job(job) if job else None, refused)
            live = repo in live_repos
            repo_days = settings.days(org, repo)
            reason = due_reason(attempt, context, days=repo_days, now=now, live=live, by_hand=by_hand)
            states.append(RepoState(context, job, refused.started_at if refused else None, reason, days=repo_days))
        return Survey(
            org.owner,
            days,
            tuple(states),
            tuple(unread),
            min(read_at) if read_at else None,
            deferred=tuple(deferred),
        )

    def _rate(self, org: Organization) -> _Rate:
        """What the forge last said of the rate limit of the credential ``org`` reads with.

        An organization with a credential of its own shares it with every one
        bound to the same; one reading with the deployment's token keeps its
        own, which its first answer fills in.
        """
        key = f"credential:{org.credential_id}" if org.credential_id else f"owner:{org.owner}"
        return self._rates.setdefault(key, _Rate())

    def _note_reserve(self, org: Organization, rate: _Rate, percent: int, now: datetime) -> None:
        """Log that ``org`` is deferred for its credential's rate reserve, once per window.

        Once until the window warned about is over, not once per window end:
        a free 304 while low is a fresh answer, and one that names no reset
        moves the window's end along with it.
        """
        if rate.noted is not None and now < rate.noted:
            return
        window = rate.until()
        rate.noted = window
        logger.warning(
            "context_audit_rate_reserve_reached",
            owner=org.owner,
            remaining=rate.remaining,
            limit=rate.limit,
            reserve_percent=percent,
            resets_at=_iso(rate.reset),
            until=_iso(window),
            hint="no paid read and no start for the organizations reading with this credential until the window ends",
        )

    def _note(self, owner: str, *, level: str = "warning", **fields: Any) -> None:
        """Log why an owner is skipped once, and again only when the reason changes."""
        said = repr(sorted(fields.items()))
        if self._noted.get(owner) == said:
            return
        self._noted[owner] = said
        getattr(logger, level)("context_audit_owner_skipped", owner=owner, **fields)

    def _note_unread(self, owner: str, unread: tuple[tuple[str, str], ...]) -> None:
        """Log each repository of ``owner`` that could not be read, once until its reason changes."""
        before = self._unread.get(owner, {})
        for repo, detail in unread:
            if before.get(repo) != detail:
                logger.warning("context_audit_repo_unread", repo=repo, detail=detail)
        self._unread[owner] = dict(unread)

    async def tick(self) -> list[str]:
        """Start a job for every due repository, at most each owner's cap, and return the repositories started.

        Every setting it reads comes from one snapshot, read once at its top
        (:meth:`settings`). Only the active registered repositories of active
        owners whose ``context_audit.days`` resolves to a number of days are
        read. Each owner's cap is its ``context_audit_max_jobs_per_tick``:
        its due repositories past it wait for the next tick, and an owner
        whose cap is 0 is not read. Each job gets the owner's
        ``context_audit_job_usd``, and opens a pull request when its
        repository's ``open_prs`` says so. An owner whose credential is down
        to ``rate_reserve_percent`` of its limit starts nothing: it is read
        again once the low window ends. An owner that cannot be read
        (a forge whose trees are not read this way, a credential that cannot
        be resolved) and a repository that cannot be read are skipped, each
        with one log line until the reason changes. The budget gate is asked
        before each start: when it refuses, the tick ends there for every
        owner, since its limits are the deployment's, and nothing counts as
        an attempt, so each repository it did not start is due again on the
        next tick. Any other start that fails is logged and counts as an
        attempt, so that repository waits a full interval unless its files
        change; the other repositories still go.
        """
        now = self._clock()
        started: list[str] = []
        organizations = await self._organizations.list_all()
        settings = await self.settings(organizations)
        self._interval = settings.interval_seconds
        # The registered repositories, read once for every owner, when an owner first needs them.
        registered: list[dict[str, Any]] | None = None
        for org in organizations:
            if org.status != "active":
                continue
            cap, budget = _caps(settings.budget.organization(org.owner, org.settings))
            if cap < 1:
                continue
            try:
                if registered is None:
                    registered = await self._registry.list_all()
                scheduled = [repo for repo in _owned(org, registered) if settings.days(org, repo) is not None]
                if not scheduled:
                    continue
                if org.forge != "github":
                    raise UnsupportedForge(_unsupported(org))
                survey = await self._survey(org, scheduled, now=now, settings=settings)
            except UnsupportedForge as exc:
                self._note(org.owner, level="info", detail=str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - one owner's failure is its own log line
                self._note(org.owner, error=type(exc).__name__, detail=_detail(exc))
                continue
            self._noted.pop(org.owner, None)
            self._note_unread(org.owner, survey.unread)
            if survey.deferred or self._rate(org).low(settings.rate_reserve_percent, now):
                # Deferred, not refused: nothing counts as an attempt, and the next tick past the reset reads it.
                continue
            count = 0
            for state in survey.repos:
                repo = state.context.repo
                if not state.due:
                    continue
                if count >= cap:
                    logger.info("context_audit_tick_full", owner=org.owner, cap=cap, next_repo=repo)
                    break
                open_pr = settings.open_prs(org, repo)
                try:
                    job_id = await self._start(state.context, budget_usd=budget, open_pr=open_pr, now=now)
                except GateRefused as exc:
                    logger.info(
                        "context_audit_gate_refused", owner=org.owner, repo=repo, limit=exc.limit, detail=exc.message
                    )
                    return started
                except Exception as exc:  # noqa: BLE001 - one repository's refusal is its own log line
                    logger.warning(
                        "context_audit_not_started",
                        repo=repo,
                        reason=state.reason,
                        error=type(exc).__name__,
                        detail=_detail(exc),
                        retry_in_days=state.days,
                    )
                    continue
                if job_id is None:
                    continue
                count += 1
                started.append(repo)
                logger.info(
                    "context_audit_started",
                    repo=repo,
                    job_id=job_id,
                    reason=state.reason,
                    files=len(state.context.files),
                    budget_usd=budget,
                )
        return started

    async def _start(
        self, context: RepoContext, *, budget_usd: float, open_pr: bool, now: datetime, run_with: Any = None
    ) -> str | None:
        """Start ``context``'s job and return its id, or None when a context-audit job of it is live.

        The survey read the jobs a moment earlier, and the tick and a start by
        hand can both have found the repository due, so the jobs are read
        again under one lock held through the start: of two such callers one
        starts the job and the other finds it live. The budget gate is asked
        next, under the same lock, so the jobs it counts include every start
        before this one: its refusal raises :class:`GateRefused` and is not
        remembered, since nothing was attempted. A start that raises is
        remembered as an attempt, the one nothing else records, and raised.
        """
        repo = context.repo
        async with self._starting:
            _newest, live = await self._audits([repo])
            if repo in live:
                logger.info("context_audit_already_live", repo=repo)
                return None
            refusal = await self._ask_gate()
            if refusal is not None:
                raise GateRefused(refusal.limit, refusal.message)
            try:
                # A run by hand may name the model and key (spec 2026-10-25, run with).
                extra = {"run_with": run_with} if run_with is not None else {}
                job_id = await self._start_job(context, budget_usd, open_pr, **extra)
            except Exception:
                self._refused[repo] = Attempt(
                    started_at=now, fingerprint=context.fingerprint, head_sha=context.head_sha
                )
                raise
            self._refused.pop(repo, None)
            return job_id

    async def run(
        self,
        org: Organization,
        *,
        repo: str | None = None,
        force: bool = False,
        limit: int | None = None,
        run_with: Any = None,
    ) -> RunResult:
        """Start the due context audits of ``org`` now, whatever its schedule says, and say what started and what not.

        Without ``repo``, every due repository, at most ``limit`` of them, the
        owner's ``context_audit_max_jobs_per_tick`` when None. With ``repo``, that one when
        it is due, or when ``force`` is true whatever the due rule says. Due
        is :func:`due_reason` with ``by_hand``: the setting may be off, and
        then no interval applies. Never while a context-audit job of the
        repository is live, ``force`` or not. Each start is :meth:`_start`, the
        tick's own, so each job records the fingerprint and head the schedule
        anchors on, and a refused start is remembered as the tick remembers
        its own. A start the budget gate refuses is :data:`GATED`, and so is
        every repository the run would have started after it; none of them
        counts as an attempt. Each job gets the owner's ``context_audit_job_usd``,
        and ``run_with``, the model and key the person chose, when given.

        Raises :class:`UnsupportedForge` as :meth:`survey` does, what
        ``client_for`` raises, :class:`UnknownRepository` for a ``repo`` that
        is not an active registered repository of ``org``, and
        :class:`InvalidRun` for ``force`` without ``repo``.
        """
        if force and repo is None:
            raise InvalidRun("force starts one repository whatever the due rule says: name it with repo")
        repos = await self._registered(org)
        if repo is not None:
            repos = [name for name in repos if name.lower() == repo.lower()]
            if not repos:
                raise UnknownRepository(f"{repo} is not an active registered repository of {org.owner}")
        now = self._clock()
        settings = await self.settings([org])
        survey = await self._survey(org, repos, now=now, settings=settings, by_hand=True)
        per_tick, budget = _caps(settings.budget.organization(org.owner, org.settings))
        cap = 1 if repo is not None else limit if limit is not None else per_tick
        # Read for free while low (a 304), but started on as the tick would not: its job spends the same credential.
        low = self._rate(org).low(settings.rate_reserve_percent, now)
        started: list[Started] = []
        skipped: list[Skipped] = []
        gated: GateRefused | None = None
        for state in survey.repos:
            name, reason = state.context.repo, state.reason
            if name in survey.deferred:
                continue  # its last reading is not started on: listed below with the deferred ones
            if reason == LIVE or (reason == CURRENT and not force):
                skipped.append(Skipped(name, reason))
                continue
            if low:
                skipped.append(Skipped(name, DEFERRED, _RESERVE_DETAIL))
                continue
            if len(started) >= cap:
                skipped.append(Skipped(name, LIMIT))
                continue
            if gated is not None:
                skipped.append(Skipped(name, GATED, str(gated)))
                continue
            try:
                job_id = await self._start(
                    state.context,
                    budget_usd=budget,
                    open_pr=settings.open_prs(org, name),
                    now=now,
                    run_with=run_with,
                )
            except GateRefused as exc:
                gated = exc
                logger.info(
                    "context_audit_gate_refused",
                    owner=org.owner,
                    repo=name,
                    limit=exc.limit,
                    detail=exc.message,
                    by_hand=True,
                )
                skipped.append(Skipped(name, GATED, str(exc)))
                continue
            except Exception as exc:  # noqa: BLE001 - one repository's refusal is its own entry
                logger.warning(
                    "context_audit_not_started",
                    repo=name,
                    reason=reason,
                    by_hand=True,
                    error=type(exc).__name__,
                    detail=_detail(exc),
                )
                skipped.append(Skipped(name, REFUSED, _detail(exc)))
                continue
            if job_id is None:
                skipped.append(Skipped(name, LIVE))
                continue
            started.append(Started(name, job_id, FORCED if reason == CURRENT else reason))
            logger.info(
                "context_audit_started",
                repo=name,
                job_id=job_id,
                reason=started[-1].reason,
                files=len(state.context.files),
                budget_usd=budget,
                by_hand=True,
            )
        skipped.extend(Skipped(name, UNREAD, detail) for name, detail in survey.unread)
        skipped.extend(Skipped(name, DEFERRED, _RESERVE_DETAIL) for name in survey.deferred)
        if repo is not None and not survey.repos and not survey.unread and not survey.deferred:
            skipped.append(Skipped(repos[0], NO_FILES))
        return RunResult(
            owner=org.owner,
            days=survey.days,
            repo=repos[0] if repo is not None else None,
            force=force,
            limit=None if repo is not None else cap,
            budget_usd=budget,
            started=tuple(started),
            skipped=tuple(skipped),
        )

    async def interval_seconds(self) -> int:
        """Seconds between two wakes, read now: the deployment's ``check_minutes``. When it cannot be read,
        the last value read, the default until one was."""
        try:
            self._interval = (await self.settings()).interval_seconds
        except Exception:  # noqa: BLE001 - the loop waits as it last read
            logger.warning("context_audit_interval_unread", seconds=self._interval, exc_info=True)
        return self._interval

    async def wait(self) -> None:
        """Sleep until the next wake is due: ``check_minutes`` after this call.

        It reads the key again at least every :data:`RECHECK_SECONDS`, so a
        shorter interval written during a longer wait applies within that,
        and a wait the default sets is one sleep of 15 minutes.
        """
        waited = 0
        while (interval := await self.interval_seconds()) > waited:
            step = min(interval - waited, RECHECK_SECONDS)
            await asyncio.sleep(step)
            waited += step

    async def start(self) -> None:
        """Wake every ``check_minutes``, from one wake's end to the next one's start, for ever."""
        while True:
            await self.wait()
            try:
                await self.tick()
            except Exception:
                logger.warning("context_audit_tick_failed", exc_info=True)


# ---- starting a job ----


class _JobStore(Protocol):
    async def create(self, **kwargs: Any) -> str: ...


class _Executor(Protocol):
    def submit_job(self, job_id: str, *, repo: str | None, title: str, body: str = "", kind: str = ...) -> Any: ...


class _Templates(Protocol):
    async def list_all(self) -> list[dict[str, Any]]: ...


def audit_task(context: RepoContext) -> str:
    """The job's task: the repository, the commit read, the instruction files found there, and the command.

    The command (:func:`~embry0.services.context_audit_command.command`) is
    built now, from the rules that selected ``context``'s files, and says how
    to list the files and how to report. It reaches the agent whatever prompt
    the deployment stored for it.
    """
    lines = [
        f"Audit the files that steer a coding agent in {context.repo} for statements that are stale or wrong.",
        "",
        f"Read at commit {context.head_sha} of the default branch.",
        "",
        f"Instruction files ({len(context.files)}):",
        *(f"- {path}" for path in context.files[:TASK_FILE_LIMIT]),
    ]
    if len(context.files) > TASK_FILE_LIMIT:
        lines.append(f"- and {len(context.files) - TASK_FILE_LIMIT} more")
    return with_command("\n".join(lines), rules=context.rules)


async def start_context_audit_job(
    context: RepoContext,
    *,
    jobs: _JobStore,
    executor: _Executor | None,
    templates: _Templates | None,
    budget_usd: float,
    open_pr: bool = False,
    db: Any = None,
    audit_log_path: Any = None,
    task: str | None = None,
    dispatched_by: str = ACTOR,
    run_with: Any = None,
    local_first_model: str | None = None,
) -> str:
    """Start one context-audit job the way ``POST /jobs`` starts a job, and return its id.

    The job row, the ``job_created`` audit row naming ``dispatched_by``, then
    the workflow on the executor. With ``open_pr`` the job opens a pull request
    from the patch its report carries (ADR 0022). Refused with :class:`LookupError` before any
    row exists while no template is named :data:`TEMPLATE`: plan_route sends
    a job whose template is missing to the default template of its kind, a
    code job, rather than failing it.

    ``task`` replaces the audit's own (a fix an owner asked for names its one
    claim) and ends with the same command, for ``context.rules``, and ``dispatched_by`` the
    schedule's actor. A fix an owner asked for is dispatched by
    :data:`FIX_ACTOR`, and that makes it no audit: the schedule's due rule
    never anchors on it (while it runs it still holds the repository) and
    the findings feed never offers its report. A job any other actor
    dispatches is an audit.
    ``run_with`` is the model and key a person chose (embry0/services/run_with.py).
    ``local_first_model`` is the model the job prefers
    (``background_models.context_audit`` of the settings document, #164),
    for a local one while it has a free slot; a model a person chose with
    ``run_with`` is a pin and wins over it.
    """
    rows = await templates.list_all() if templates is not None else []
    if not any(row.get("name") == TEMPLATE for row in rows):
        raise LookupError(f"no pipeline template named {TEMPLATE!r}")
    if executor is None:
        raise RuntimeError("executor not initialized")
    task = with_command(task, rules=context.rules) if task else audit_task(context)
    pipeline_config = {
        # Nobody watches a scheduled audit, so its cap is a hard stop
        # whatever the fleet-wide overrun mode is, as the watcher's (#275).
        "max_budget_usd": budget_usd,
        "budget_overrun_mode": "hard",
        # What the due rule compares the next reading with.
        "context_fingerprint": context.fingerprint,
        "head_sha": context.head_sha,
        "actor": dispatched_by,
        # Read by the executor into the job's state; the output step reads it there.
        "open_pr": open_pr,
    } | (apply_run_with(None, run_with) or {})
    if local_first_model and not pipeline_config.get("agent_models_override"):
        pipeline_config["local_first_model"] = local_first_model
    job_id = await jobs.create(
        repo=context.repo,
        task=task,
        context={"type": "git", "repo": context.repo},
        pipeline_template=TEMPLATE,
        pipeline_config=pipeline_config,
        trace_id=f"trc-{uuid.uuid4().hex[:12]}",
        dispatched_by=dispatched_by,
    )
    await emit_audit(
        db,
        "job_created",
        actor=dispatched_by,
        details={"job_id": job_id, "repo": context.repo, "pipeline_template": TEMPLATE},
        audit_log_path=audit_log_path,
    )
    executor.submit_job(job_id, repo=context.repo, title=task.splitlines()[0], body=task, kind="context_audit")
    return job_id
