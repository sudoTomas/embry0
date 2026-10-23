"""The context audit on a schedule (ADR 0020).

A coding agent takes the files that steer it (``CLAUDE.md``, ``AGENTS.md``,
a skill, a rules directory: :mod:`embry0.services.instruction_files`) as
instructions, so a line in one that went stale reaches the agent as a fact.
An organization whose ``settings.context_audit_days`` is set has those files
audited without anyone starting a job: one loop in the orchestrator wakes
every :data:`TICK_SECONDS`, reads the default branch of each active
registered repository under each such owner with the organization's
credential, and starts a :data:`TEMPLATE` job for each repository that is
due (:func:`due_reason`), at most ``max_jobs_per_tick`` per wake.

The schedule is anchored on the recorded jobs, never on the process clock,
so a restart neither postpones a due audit nor repeats a recent one. The
newest job counts whatever became of it, a failed one included, and so does
a start refused before any job existed (the template missing, the executor
down): the loop keeps the time of each repository's last refusal. Either way
a repository whose files did not change is tried again one interval later,
not on every tick. The refusals live in the process, so a restart tries such
a repository once more.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import uuid
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from embry0.audit.helpers import emit_audit
from embry0.orchestration import dispatch_actors
from embry0.services.instruction_files import instruction_files
from embry0.services.loop_schedule import seconds_until_next_run
from embry0.services.organizations import Organization, context_audit_days

logger = structlog.get_logger(__name__)

#: The pipeline template every scheduled job names.
TEMPLATE = "context-audit"

#: The actor of every job the schedule starts, in ``jobs.dispatched_by`` and
#: in the job's audit row.
ACTOR = dispatch_actors.CONTEXT_AUDIT

#: How often the loop wakes. A wake reads the forge, one commit listing per
#: repository and its tree when the head moved, so it is longer than
#: rediscovery's, and still short against an interval of days.
TICK_SECONDS = 900

#: The per-job cap when ``CONTEXT_AUDIT_JOB_BUDGET_USD`` is unset or invalid,
#: the watcher's cap for an analysis job, a job of the same size.
DEFAULT_JOB_BUDGET_USD = 2.0

#: Files the task names one by one; past it the task says how many more.
TASK_FILE_LIMIT = 200

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


@dataclass(frozen=True)
class RepoContext:
    """A repository's instruction files at the head of its default branch."""

    repo: str
    head_sha: str
    files: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True)
class Attempt:
    """The newest context audit of a repository, as the due rule reads it."""

    started_at: datetime
    #: When it completed; None for any other outcome, which counts from its start.
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
            finished_at=_when(row.get("finished_at")) if row.get("status") == "completed" else None,
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


def due_reason(
    newest: Attempt | None, current: RepoContext, *, days: int | None, now: datetime, live: bool = False
) -> str:
    """Why a repository whose newest context audit is ``newest`` is due at ``now``, or why not.

    In this order: :data:`OFF` when ``days`` is None; :data:`LIVE` while a
    context-audit job of it has not finished; :data:`FIRST` with no audit
    yet; :data:`CHANGED` when the files differ from those the newest audit
    recorded (one that recorded none, such as a job a person started, is not
    compared); :data:`AGED` when the newest audit is ``days`` old and the
    branch head moved since; else :data:`CURRENT`. The age is read as
    :func:`~embry0.services.loop_schedule.seconds_until_next_run` reads its
    anchors: a completed audit from when it finished, any other (failed,
    cancelled, refused before it existed) from when it started, so a failure
    waits a full interval like a success.
    """
    if days is None:
        return OFF
    if live:
        return LIVE
    if newest is None:
        return FIRST
    if newest.fingerprint is not None and newest.fingerprint != current.fingerprint:
        return CHANGED
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


class ForgeReadError(RuntimeError):
    """The forge did not answer a read the way the audit needs."""


class _Forge(Protocol):
    async def get(self, path: str, **kwargs: Any) -> Any: ...


def _json(resp: Any) -> Any:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is "nothing"
        return None


async def read_head(client: _Forge, repo: str) -> tuple[str, str] | None:
    """The head commit sha and tree sha of the default branch; None for an empty repository.

    The commit listing starts at the default branch when no ref is given, so
    its first entry is that branch's head. No ``repo`` is passed to the
    client, so it acts with the token it was built with, the organization's.
    """
    resp = await client.get(f"/repos/{repo}/commits", params={"per_page": 1})
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
    return sha, tree


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

    @property
    def due(self) -> bool:
        return self.reason in DUE_REASONS

    def to_dict(self) -> dict[str, Any]:
        job = self.newest_job
        newest = None
        if job is not None:
            newest = {
                "job_id": job.get("job_id"),
                "status": job.get("status"),
                "created_at": _iso(job.get("created_at")),
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
        }


@dataclass(frozen=True)
class Survey:
    """An organization's repositories as the schedule reads them."""

    owner: str
    days: int | None
    repos: tuple[RepoState, ...]
    #: Repositories whose head or tree could not be read, with why.
    unread: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "context_audit_days": self.days,
            "template": TEMPLATE,
            "repos": [state.to_dict() for state in self.repos],
            "unread": [{"repo": repo, "detail": detail} for repo, detail in self.unread],
        }


class _Organizations(Protocol):
    async def list_all(self, *, include_removed: bool = False) -> list[Organization]: ...


class _Registry(Protocol):
    async def list_all(self, include_inactive: bool = False) -> list[dict[str, Any]]: ...


class _Jobs(Protocol):
    async def newest_per_repo(
        self, repos: Collection[str], *, pipeline_template: str, dispatched_by: str | None = None
    ) -> dict[str, dict[str, Any]]: ...


#: The client that reads an organization's repositories with its credential.
ClientFor = Callable[[Organization], Awaitable[_Forge]]

#: Starts one context-audit job and returns its id.
StartJob = Callable[[RepoContext], Awaitable[str]]


def _detail(exc: Exception) -> str:
    """What a refusal says: an HTTPException's detail, else the exception's text, else its type."""
    text = str(getattr(exc, "detail", "") or "") or str(exc)
    return (text or type(exc).__name__)[:200]


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
        max_jobs_per_tick: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self._organizations = organizations
        self._registry = registry
        self._jobs = jobs
        self._client_for = client_for
        self._start_job = start_job
        self._max_jobs_per_tick = max_jobs_per_tick
        self._clock = clock
        self._tick_seconds = tick_seconds
        # Each repository's last start refused before a job row existed, the
        # one attempt nothing else records.
        self._refused: dict[str, Attempt] = {}
        # The last reading of each repository: a head that has not moved has
        # the same tree, so it is not read again.
        self._contexts: dict[str, RepoContext] = {}
        # What was last logged about each owner skipped, and about each
        # repository of an owner its last read could not read, so a standing
        # problem is one log line, not one per tick.
        self._noted: dict[str, str] = {}
        self._unread: dict[str, dict[str, str]] = {}

    async def _read(self, client: _Forge, repo: str) -> RepoContext | None:
        head = await read_head(client, repo)
        if head is None:
            return None
        head_sha, tree_sha = head
        cached = self._contexts.get(repo)
        if cached is not None and cached.head_sha == head_sha:
            return cached
        blobs = await read_blobs(client, repo, tree_sha)
        files = instruction_files(blobs)
        context = RepoContext(
            repo=repo,
            head_sha=head_sha,
            files=tuple(files),
            fingerprint=fingerprint({path: blobs[path] for path in files}),
        )
        self._contexts[repo] = context
        return context

    async def survey(self, org: Organization, *, now: datetime | None = None) -> Survey:
        """Every active registered repository of ``org`` with instruction files, and whether each is due.

        Reads the forge and the jobs table and starts nothing; the read
        endpoint answers with it. A repository with no instruction files, or
        no commit, is left out, and one whose reads failed is listed in
        ``unread``. Raises :class:`UnsupportedForge` for an owner on a forge
        whose trees are not read this way, and what ``client_for`` raises
        when the owner's credential cannot be resolved.
        """
        if org.forge != "github":
            raise UnsupportedForge(f"the context audit reads GitHub repositories in this release, not {org.forge}")
        now = now or self._clock()
        days = context_audit_days(org.settings)
        repos = [
            str(row["repo"])
            for row in await self._registry.list_all()
            if str(row["repo"]).split("/", 1)[0].lower() == org.owner and row.get("forge") == org.forge
        ]
        if not repos:
            return Survey(org.owner, days, ())
        client = await self._client_for(org)
        newest = await self._jobs.newest_per_repo(repos, pipeline_template=TEMPLATE, dispatched_by=ACTOR)
        states: list[RepoState] = []
        unread: list[tuple[str, str]] = []
        for repo in repos:
            try:
                context = await self._read(client, repo)
            except Exception as exc:  # noqa: BLE001 - one repository's failure is its own entry
                unread.append((repo, str(exc) if isinstance(exc, ForgeReadError) else type(exc).__name__))
                continue
            if context is None or not context.files:
                continue
            job = newest.get(repo)
            refused = self._refused.get(repo)
            attempt = _newer(Attempt.from_job(job) if job else None, refused)
            reason = due_reason(attempt, context, days=days, now=now, live=bool(job and job.get("live")))
            states.append(RepoState(context, job, refused.started_at if refused else None, reason))
        return Survey(org.owner, days, tuple(states), tuple(unread))

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
        """Start a job for every due repository, at most ``max_jobs_per_tick``, and return the repositories started.

        Only active owners with ``context_audit_days`` set are read. An owner
        that cannot be read (a forge whose trees are not read this way, a
        credential that cannot be resolved) and a repository that cannot be
        read are skipped, each with one log line until the reason changes. A
        start that fails is logged and counts as an attempt, so that
        repository waits a full interval unless its files change; the other
        repositories still go. Once the cap is reached the owners not yet
        read wait for the next tick.
        """
        cap = self._max_jobs_per_tick
        if cap < 1:
            return []
        now = self._clock()
        started: list[str] = []
        for org in await self._organizations.list_all():
            days = context_audit_days(org.settings)
            if org.status != "active" or days is None:
                continue
            if len(started) >= cap:
                logger.info("context_audit_tick_full", cap=cap, next_owner=org.owner)
                return started
            try:
                survey = await self.survey(org, now=now)
            except UnsupportedForge as exc:
                self._note(org.owner, level="info", detail=str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - one owner's failure is its own log line
                self._note(org.owner, error=type(exc).__name__, detail=_detail(exc))
                continue
            self._noted.pop(org.owner, None)
            self._note_unread(org.owner, survey.unread)
            for state in survey.repos:
                repo = state.context.repo
                if not state.due:
                    continue
                if len(started) >= cap:
                    logger.info("context_audit_tick_full", cap=cap, next_repo=repo)
                    return started
                try:
                    job_id = await self._start_job(state.context)
                except Exception as exc:  # noqa: BLE001 - one repository's refusal is its own log line
                    self._refused[repo] = Attempt(
                        started_at=now, fingerprint=state.context.fingerprint, head_sha=state.context.head_sha
                    )
                    logger.warning(
                        "context_audit_not_started",
                        repo=repo,
                        reason=state.reason,
                        error=type(exc).__name__,
                        detail=_detail(exc),
                        retry_in_days=days,
                    )
                    continue
                self._refused.pop(repo, None)
                started.append(repo)
                logger.info(
                    "context_audit_started",
                    repo=repo,
                    job_id=job_id,
                    reason=state.reason,
                    files=len(state.context.files),
                )
        return started

    async def start(self) -> None:  # pragma: no cover - exercised by the app
        while True:
            await asyncio.sleep(self._tick_seconds)
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


def job_budget_usd(config: Any) -> float:
    """The per-job cap: ``CONTEXT_AUDIT_JOB_BUDGET_USD`` when it is a positive finite number, else the default.

    0 means unlimited elsewhere in the budget config, and ``inf`` amounts to
    the same; an unattended job never gets that reading.
    """
    value: Any = getattr(config, "context_audit_job_budget_usd", None)
    try:
        cap = float(value)
    except (TypeError, ValueError):
        return DEFAULT_JOB_BUDGET_USD
    return cap if math.isfinite(cap) and cap > 0 else DEFAULT_JOB_BUDGET_USD


def audit_task(context: RepoContext) -> str:
    """The job's task: the repository, the commit read, and the instruction files found there."""
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
    return "\n".join(lines)


async def start_context_audit_job(
    context: RepoContext,
    *,
    jobs: _JobStore,
    executor: _Executor | None,
    templates: _Templates | None,
    budget_usd: float,
    db: Any = None,
    audit_log_path: Any = None,
) -> str:
    """Start one context-audit job the way ``POST /jobs`` starts a job, and return its id.

    The job row, the ``job_created`` audit row naming :data:`ACTOR`, then the
    workflow on the executor. Refused with :class:`LookupError` before any
    row exists while no template is named :data:`TEMPLATE`: plan_route sends
    a job whose template is missing to the default template of its kind, a
    code job, rather than failing it.
    """
    rows = await templates.list_all() if templates is not None else []
    if not any(row.get("name") == TEMPLATE for row in rows):
        raise LookupError(f"no pipeline template named {TEMPLATE!r}")
    if executor is None:
        raise RuntimeError("executor not initialized")
    task = audit_task(context)
    job_id = await jobs.create(
        repo=context.repo,
        task=task,
        context={"type": "git", "repo": context.repo},
        pipeline_template=TEMPLATE,
        pipeline_config={
            # Nobody watches a scheduled audit, so its cap is a hard stop
            # whatever the fleet-wide overrun mode is, as the watcher's (#275).
            "max_budget_usd": budget_usd,
            "budget_overrun_mode": "hard",
            # What the due rule compares the next reading with.
            "context_fingerprint": context.fingerprint,
            "head_sha": context.head_sha,
            "actor": ACTOR,
        },
        trace_id=f"trc-{uuid.uuid4().hex[:12]}",
        dispatched_by=ACTOR,
    )
    await emit_audit(
        db,
        "job_created",
        actor=ACTOR,
        details={"job_id": job_id, "repo": context.repo, "pipeline_template": TEMPLATE},
        audit_log_path=audit_log_path,
    )
    executor.submit_job(job_id, repo=context.repo, title=task.splitlines()[0], body=task, kind="context_audit")
    return job_id
