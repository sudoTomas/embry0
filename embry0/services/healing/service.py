"""HealingCaseService — the one write path into the lifecycle ledger (#278).

Every writer (watcher, tracker sync, forge webhooks, deployer,
confirm-healed) records what it saw through the methods here and nothing
else. Each method is one fact about one case:

    open_case · attach_tracker · attach_issue · attach_job
    attach_pr · close_pr · record_merge · record_deploy
    record_verdict · record_recurrence · reopen · close

Each call resolves its event through ``lifecycle.transition`` while holding
the case row locked, then writes stage, data, child rows and a history row
in one transaction. How the data lands follows the transition's effect:

* ``APPLIED``    — the stage changes; the call's data replaces what was
  there and ``<stage>_at`` is stamped (a move BACK to a stage keeps the
  first stamp, so funnel timings survive a closed PR; a reopen restarts
  the wait and writes its own).
* ``REPEAT``     — a redelivery. Values that differ are written, except the
  call's *sticky* columns (a merge's sha and resolution path, a deploy's
  anchor): those are only ever filled, never replaced. A redelivery that
  disagrees with a sticky column is kept as a history row
  (``detail.not_applied``) and logged, not applied.
* ``LATE``       — the case is already past this stage; the call fills
  columns that are still empty, and dates ``<stage>_at`` if nothing has.
* ``NOTED``      — an event that stands for no stage; its data is written.
* ``POST_CLOSE`` — the case is closed and frozen. Child facts (a job link,
  a PR's outcome) and a history row are kept; the case row is not touched.

An identical redelivery writes nothing at all, and a call carrying an
``event_key`` the case has already seen (a forge delivery id) is dropped
whole. Callers therefore need not order or dedupe their events; they should
pass ``at`` (when it happened, per the source) wherever the source says.

Every write returns a :class:`CaseResult` — the case row, plus ``.outcome``
saying how the call landed (``applied`` / ``unchanged`` / ``duplicate`` /
``duplicate_conflict``).

``verified`` and ``closed`` do NOT mean a fix shipped — use
``lifecycle.fix_deployed``.

**What may be written here.** History rows copy the call's data and
``GET /cases/{id}`` and the ``case_get`` MCP tool serve them, so this
interface is not a place for log content. ``evidence`` is COUNTS and
QUERIES (``{"logql": ..., "before": 40, "after": 0, "window_s": 3600}``),
never raw log lines; it is refused above ``MAX_EVIDENCE_BYTES``. Free-text
fields (``MAX_TEXT``) are truncated at this boundary.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import asyncpg
import structlog

from embry0.metrics import NULL_METRICS, LoopMetrics
from embry0.services.healing.errors import CaseConflictError, CaseNotFoundError, CaseReferenceError
from embry0.services.healing.lifecycle import (
    CaseEvent,
    Effect,
    Origin,
    ResolutionPath,
    SignatureSource,
    Stage,
    Verdict,
    transition,
)
from embry0.storage.repositories.healing_cases import HealingCasesRepository, LockedCase

logger = structlog.get_logger(__name__)

# Metric events of the write in progress: (event, effect, resolution_path).
# Counted only once the case's transaction has committed — see ``_locked``.
_PENDING_METRICS: ContextVar[list[tuple[str, str, str | None]] | None] = ContextVar(
    "healing_case_pending_metrics", default=None
)

# The paths a merge can resolve a case by; the others belong to close().
_MERGE_PATHS = (ResolutionPath.EMBRY0, ResolutionPath.OUT_OF_BAND)


# Free text is truncated to these lengths at the service boundary; evidence
# (structured) is refused rather than truncated.
MAX_TEXT: dict[str, int] = {
    "title": 500,
    "note": 2000,
    "reason": 1000,
    "severity": 64,
    "fixability": 64,
}
MAX_EVIDENCE_BYTES = 16_384
# The signature is a HUMAN-READABLE identifier for the fault, not a query:
# a cluster template for a watcher finding (#290), a PromQL expression for an
# alert (#296). Confirm-healed (#312) does NOT execute it — it counts through
# `log_signatures`, keyed on signature_id. Still refused rather than truncated
# when over-long, because a half a signature identifies the wrong thing.
MAX_SIGNATURE = 4000
# Timestamps from other hosts may run a little ahead of this one.
MAX_CLOCK_SKEW = timedelta(minutes=5)
_TRUNCATED = "…[truncated]"
# A closed case's recurrences share one history row; it remembers this many
# recent event keys so a redelivery is not counted twice.
_POST_CLOSE_KEYS = 50
_DEPLOY_COLUMNS = ("deploy_sha", "deploy_marker_at", "deploy_services")


class WriteOutcome(StrEnum):
    APPLIED = "applied"  # something was written
    UNCHANGED = "unchanged"  # nothing new in the call
    DUPLICATE = "duplicate"  # event_key already seen with this payload: dropped
    DUPLICATE_CONFLICT = "duplicate_conflict"  # event_key already seen with ANOTHER payload: dropped, logged


class CaseResult(dict[str, Any]):
    """The case row after a write, plus how the write landed."""

    outcome: WriteOutcome

    def __init__(self, row: dict[str, Any], outcome: WriteOutcome) -> None:
        super().__init__(row)
        self.outcome = outcome


@dataclass(frozen=True)
class _Delivery:
    """A call's idempotency key and a fingerprint of what it asked for."""

    event_key: str | None
    request: str


def _delivery(event_key: str | None, method: str, **payload: Any) -> _Delivery:
    body = json.dumps({"method": method, **_json_safe(payload)}, sort_keys=True, default=str)
    return _Delivery(event_key, hashlib.sha256(body.encode()).hexdigest()[:16])


def _checked(name: str, value: datetime | None) -> datetime | None:
    """Refuse a timestamp the ledger's time rules cannot work with.

    Anchoring compares a deploy's time with its merge's, so one naive value
    (compared in whatever zone the server is in) or one far-future value (a
    merge dated 2099 leaves every real deploy un-anchorable for good) would
    corrupt a case silently.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    if value > datetime.now(UTC) + MAX_CLOCK_SKEW:
        raise ValueError(f"{name} is in the future ({value.isoformat()}); pass when it happened, per the source")
    return value


def _cap(name: str, value: str | None) -> str | None:
    limit = MAX_TEXT[name]
    if value is None or len(value) <= limit:
        return value
    return value[: limit - len(_TRUNCATED)] + _TRUNCATED


@dataclass(frozen=True)
class TrackerRef:
    """A ticket in an external tracker, e.g. ``linear`` / ``RAV-1988``."""

    kind: str
    key: str
    url: str | None = None


def _json_safe(fields: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in fields.items()}


class HealingCaseService:
    def __init__(self, repo: HealingCasesRepository, *, metrics: LoopMetrics | None = None) -> None:
        self._repo = repo
        self._metrics = metrics or NULL_METRICS

    # ------------------------------------------------------------------- reads

    async def get(self, case_id: str) -> dict[str, Any] | None:
        """The case with its ``jobs``, ``prs`` and ``events``, or None."""
        return await self._repo.get_detail(case_id)

    async def find_by_tracker(self, kind: str, key: str, repo: str | None = None) -> dict[str, Any] | None:
        """The ticket's open case, else its newest closed one."""
        return await self._repo.find_by_tracker(kind, key, repo)

    async def find_by_issue(self, issue_id: str) -> dict[str, Any] | None:
        return await self._repo.find_by_issue(issue_id)

    async def find_by_job(self, job_id: str) -> dict[str, Any] | None:
        """The case a pipeline job is working (linked by ``attach_job``)."""
        return await self._repo.find_by_job(job_id)

    async def find_by_pr_url(self, pr_url: str) -> dict[str, Any] | None:
        """The case owning this PR — any PR it ever had."""
        return await self._repo.find_by_pr_url(pr_url)

    async def find_by_fingerprint(
        self, fingerprint: str, *, origin: Origin | str, repo: str | None = None
    ) -> dict[str, Any] | None:
        """The newest case a detector's dedup fingerprint names — open first."""
        return await self._repo.find_by_fingerprint(fingerprint, origin=Origin(origin).value, repo=repo)

    async def find_by_signature(
        self, signature_id: str, *, repo: str | None = None, origin: Origin | str | None = None
    ) -> dict[str, Any] | None:
        """The case a log signature belongs to — open first, no time bound (#290).

        ``origin`` is optional here where :meth:`find_by_fingerprint` requires
        it: a fingerprint is a per-detector hash and only means something
        inside its own origin, but a signature id comes from the log line and
        is the same fault whoever noticed it.
        """
        return await self._repo.find_by_signature(
            signature_id, repo=repo, origin=Origin(origin).value if origin is not None else None
        )

    async def open_signature_ids(self, *, repo: str | None = None) -> set[str]:
        """Signature ids that currently have an open case — one round trip."""
        return await self._repo.open_signature_ids(repo=repo)

    async def metrics_snapshot(self) -> dict[str, Any]:
        """Counts for the Prometheus gauges — see the repository method."""
        return await self._repo.metrics_snapshot()

    # ------------------------------------------------------------------ writes

    async def open_case(
        self,
        *,
        repo: str,
        origin: Origin | str,
        title: str,
        signature: str | None = None,
        signature_id: str | None = None,
        signature_source: SignatureSource | str | None = None,
        fingerprint: str | None = None,
        severity: str | None = None,
        fixability: str | None = None,
        watcher_run_id: str | None = None,
        tracker: TrackerRef | None = None,
        parent_case_id: str | None = None,
        note: str | None = None,
        at: datetime | None = None,
        work_nature: dict[str, Any] | None = None,
    ) -> CaseResult:
        """Open a case, or return the one that already owns this watcher run
        or is OPEN for this tracker ticket. A case opened with a tracker
        starts ``tracked``.

        A ticket whose earlier case is closed gets a NEW case — a regression,
        a reopened or a reused ticket — linked to that predecessor through
        ``parent_case_id`` unless the caller names a different parent.
        ``signature`` is a query (LogQL), not log output; it will be
        executed, so one longer than ``MAX_SIGNATURE`` is refused, never
        truncated. ``signature_source`` says how the watcher chose
        ``signature_id`` (#657); confirm-healed answers ``inconclusive`` for
        a ``fallback`` one. Timestamps on every write must be timezone-aware
        and not in the future (``MAX_CLOCK_SKEW`` tolerated).
        """
        origin = Origin(origin)
        source = SignatureSource(signature_source) if signature_source is not None else None
        if signature is not None and len(signature) > MAX_SIGNATURE:
            raise ValueError(
                f"signature is {len(signature)} characters (limit {MAX_SIGNATURE}): it is executed as a query, "
                "so it is refused rather than truncated"
            )
        stage = Stage.TRACKED if tracker else Stage.DETECTED
        when = _checked("at", at) or datetime.now(UTC)
        if tracker and parent_case_id is None:
            predecessor = await self._repo.find_by_tracker(tracker.kind, tracker.key, repo)
            if predecessor is not None and predecessor["stage"] == Stage.CLOSED:
                parent_case_id = predecessor["id"]

        columns: dict[str, Any] = {
            "repo": repo,
            "origin": origin.value,
            "title": _cap("title", title),
            "stage": stage.value,
            "resolution_path": None,
            "severity": _cap("severity", severity),
            "fixability": _cap("fixability", fixability),
            "signature": signature,
            "signature_id": signature_id,
            "signature_source": source.value if source is not None else None,
            "fingerprint": fingerprint,
            "occurrence_count": 1,
            "last_seen_at": when,
            "watcher_run_id": watcher_run_id,
            "tracker": tracker.kind if tracker else None,
            "tracker_key": tracker.key if tracker else None,
            "tracker_url": tracker.url if tracker else None,
            "healed_verdict": Verdict.PENDING.value,
            "parent_case_id": parent_case_id,
            "note": _cap("note", note),
            "detected_at": when,
            "tracked_at": when if tracker else None,
        }
        # Slop dungeon (migration 118): the watcher's slopometer verdict. Left
        # out when absent, so a repository without the column still opens cases.
        if work_nature is not None:
            columns["work_nature"] = work_nature
        try:
            created = await self._repo.create_with_history(columns)
        except asyncpg.ForeignKeyViolationError as exc:
            raise CaseReferenceError(
                f"cannot open case: watcher run {watcher_run_id!r} or parent case {parent_case_id!r} does not exist"
            ) from exc
        if created is not None:
            self._metrics.case_opened(created["origin"])
            return CaseResult(created, WriteOutcome.APPLIED)

        existing = None
        if watcher_run_id:
            existing = await self._repo.find_by_watcher_run(watcher_run_id)
        if existing is None and tracker:
            existing = await self._repo.find_by_tracker(tracker.kind, tracker.key, repo)
        if existing is None:  # pragma: no cover — the conflicting row vanished between the two statements
            raise CaseConflictError("case insert conflicted but no owning case was found")
        return CaseResult(existing, WriteOutcome.UNCHANGED)

    async def attach_tracker(
        self, case_id: str, tracker: TrackerRef, *, at: datetime | None = None, event_key: str | None = None
    ) -> CaseResult:
        _checked("at", at)
        delivery = _delivery(event_key, "attach_tracker", kind=tracker.kind, key=tracker.key, url=tracker.url)
        async with self._locked(case_id) as found:
            case = _require(found, case_id)
            if (dup := await self._duplicate(case, delivery)) is not None:
                return dup
            held = (case.row["tracker"], case.row["tracker_key"])
            if held[0] is not None and held != (tracker.kind, tracker.key):
                raise CaseConflictError(
                    f"case {case_id} already tracks {held[0]}/{held[1]}; refusing to swap it for "
                    f"{tracker.kind}/{tracker.key}"
                )
            fields = {"tracker": tracker.kind, "tracker_key": tracker.key, "tracker_url": tracker.url}
            try:
                return await self._apply(case, CaseEvent.TRACKER_ATTACHED, fields, at, delivery=delivery)
            except asyncpg.UniqueViolationError as exc:
                raise CaseConflictError(f"{tracker.kind}/{tracker.key} already has an open case") from exc

    async def attach_issue(
        self, case_id: str, issue_id: str, *, at: datetime | None = None, event_key: str | None = None
    ) -> CaseResult:
        """Link the embry0 issue. An issue means the finding was accepted."""
        _checked("at", at)
        delivery = _delivery(event_key, "attach_issue", issue_id=issue_id)
        async with self._locked(case_id) as found:
            case = _require(found, case_id)
            if (dup := await self._duplicate(case, delivery)) is not None:
                return dup
            _refuse_issue_swap(case, issue_id)
            try:
                return await self._apply(case, CaseEvent.ACCEPTED, {"issue_id": issue_id}, at, delivery=delivery)
            except asyncpg.ForeignKeyViolationError as exc:
                raise CaseReferenceError(f"issue {issue_id!r} does not exist") from exc

    async def attach_job(
        self, case_id: str, job_id: str, *, at: datetime | None = None, event_key: str | None = None
    ) -> CaseResult:
        """Link a pipeline job (a case collects many: retries, re-dispatch).
        The job's issue is linked too when the case has none yet."""
        _checked("at", at)
        delivery = _delivery(event_key, "attach_job", job_id=job_id)
        async with self._locked(case_id) as found:
            case = _require(found, case_id)
            if (dup := await self._duplicate(case, delivery)) is not None:
                return dup
            exists, issue_id = await case.job_issue_id(job_id)
            if not exists:
                raise CaseReferenceError(f"job {job_id!r} does not exist")
            fields: dict[str, Any] = {}
            if issue_id is not None:
                _refuse_issue_swap(case, issue_id)
                fields["issue_id"] = issue_id
            linked = await case.link_job(job_id, at or datetime.now(UTC))
            return await self._apply(
                case, CaseEvent.ACCEPTED, fields, at, detail={"job_id": job_id}, changed=linked, delivery=delivery
            )

    async def attach_pr(
        self,
        case_id: str,
        *,
        url: str,
        number: int | None = None,
        head_sha: str | None = None,
        job_id: str | None = None,
        reopened: bool = False,
        at: datetime | None = None,
        event_key: str | None = None,
    ) -> CaseResult:
        """Record a PR of the case, or news about one it already has.

        A case keeps every PR it ever had. Call again on a push with the new
        ``head_sha`` — which REQUIRES ``at``, the push's own time per the
        forge: an older redelivered push never rewinds the sha, and "now"
        would beat every real push time. A PR this case knows as closed or
        merged stays so; pass ``reopened=True`` for a genuine forge reopen.
        """
        if head_sha is not None and at is None:
            raise ValueError("attach_pr(head_sha=...) needs `at`, the time of that push according to the forge")
        when = at or datetime.now(UTC)
        _checked("at", at)
        delivery = _delivery(
            event_key, "attach_pr", url=url, number=number, head_sha=head_sha, job_id=job_id, reopened=reopened
        )
        async with self._locked(case_id) as found:
            case = _require(found, case_id)
            if (dup := await self._duplicate(case, delivery)) is not None:
                return dup
            pr = await self._owned_pr(case, url)
            changes: dict[str, Any]
            try:
                if pr is None:
                    await case.insert_pr(
                        url=url, number=number, head_sha=head_sha, state="open", merge_sha=None, job_id=job_id, at=when
                    )
                    event, changes = CaseEvent.PR_OPENED, {"state": "open"}
                else:
                    changes = {}
                    if pr["number"] is None and number is not None:
                        changes["number"] = number
                    if pr["job_id"] is None and job_id is not None:
                        changes["job_id"] = job_id
                    newer = pr["head_sha_at"] is None or when >= pr["head_sha_at"]
                    if head_sha is not None and head_sha != pr["head_sha"] and newer:
                        changes.update(head_sha=head_sha, head_sha_at=when)
                    event = CaseEvent.PR_UPDATED
                    if reopened and pr["state"] == "closed":
                        changes.update(state="open", closed_at=None)
                        event = CaseEvent.PR_OPENED
                    await case.update_pr(pr["id"], changes)
            except asyncpg.ForeignKeyViolationError as exc:
                raise CaseReferenceError(f"job {job_id!r} does not exist") from exc
            return await self._apply(
                case,
                event,
                {},
                at,
                detail={"pr_url": url, "pr": _json_safe(changes)},
                changed=bool(changes),
                delivery=delivery,
            )

    async def close_pr(
        self, case_id: str, *, url: str, at: datetime | None = None, event_key: str | None = None
    ) -> CaseResult:
        """Record that a PR was closed WITHOUT merging. A ``pr_open`` case
        goes back to ``accepted`` unless another of its PRs is still open.
        Safe to call for a merge's ``closed`` webhook: a merged PR stays merged."""
        when = at or datetime.now(UTC)
        _checked("at", at)
        delivery = _delivery(event_key, "close_pr", url=url)
        async with self._locked(case_id) as found:
            case = _require(found, case_id)
            if (dup := await self._duplicate(case, delivery)) is not None:
                return dup
            pr = await self._owned_pr(case, url)
            changed = True
            if pr is None:
                await case.insert_pr(
                    url=url, number=None, head_sha=None, state="closed", merge_sha=None, job_id=None, at=when
                )
            elif pr["state"] == "open":
                await case.update_pr(pr["id"], {"state": "closed", "closed_at": when})
            elif pr["state"] == "merged":
                return CaseResult(case.row, WriteOutcome.UNCHANGED)
            else:
                changed = False
            return await self._apply(
                case,
                CaseEvent.PR_CLOSED,
                {},
                at,
                detail={"pr_url": url},
                changed=changed,
                other_open_prs=await case.has_open_pr_besides(url),
                delivery=delivery,
            )

    async def record_merge(
        self,
        case_id: str,
        *,
        merge_sha: str | None,
        resolution_path: ResolutionPath | str,
        pr_url: str | None = None,
        at: datetime | None = None,
        event_key: str | None = None,
    ) -> CaseResult:
        """Record the merge that fixes the finding.

        ``resolution_path`` has no default on purpose: the caller must say
        whether embry0 or a human (``out_of_band``, D4) made the fix, and the
        first answer stands — a redelivery cannot flip it. An out-of-band
        merge is legal from any open stage, and the case still goes on to a
        deploy and a verdict. ``merge_sha`` may be None when only the fact of
        the merge is known (historical rows); a later call fills it in.
        ``pr_url`` marks that PR merged, adding it to the case if unknown.

        Pass ``at`` = the forge's merge time, not the webhook's arrival: a
        deploy that reached embry0 BEFORE this call was kept in the history,
        and the earliest kept deploy at or after ``at`` becomes the anchor
        here. With ``at`` left to default to now, no earlier deploy can
        qualify.
        """
        path = ResolutionPath(resolution_path)
        if path not in _MERGE_PATHS:
            raise ValueError(f"a merge resolves a case as one of {[p.value for p in _MERGE_PATHS]}, not {path.value!r}")
        when = at or datetime.now(UTC)
        _checked("at", at)
        delivery = _delivery(event_key, "record_merge", merge_sha=merge_sha, resolution_path=path.value, pr_url=pr_url)
        async with self._locked(case_id) as found:
            case = _require(found, case_id)
            if (dup := await self._duplicate(case, delivery)) is not None:
                return dup
            changed = pr_already_merged = False
            if pr_url is not None:
                pr = await self._owned_pr(case, pr_url)
                pr_already_merged = pr is not None and pr["state"] == "merged"
                if pr is None:
                    await case.insert_pr(
                        url=pr_url,
                        number=None,
                        head_sha=None,
                        state="merged",
                        merge_sha=merge_sha,
                        job_id=None,
                        at=when,
                    )
                    changed = True
                else:
                    merge_changes: dict[str, Any] = {}
                    if pr["state"] != "merged":
                        merge_changes.update(state="merged", merged_at=when, closed_at=None)
                    if pr["merge_sha"] is None and merge_sha is not None:
                        merge_changes["merge_sha"] = merge_sha
                    await case.update_pr(pr["id"], merge_changes)
                    changed = bool(merge_changes)
            had_merge = case.row["merged_at"] is not None
            fields = {"merge_sha": merge_sha, "resolution_path": path.value}
            detail = {"pr_url": pr_url} if pr_url else None
            if not await self._merge_is_current(case, merge_sha, pr_already_merged, when):
                # A redelivery of a fix attempt that `reopen` discarded: kept
                # as history, never the retry's merge.
                return await self._apply(
                    case, CaseEvent.MERGED, {}, at, detail=detail, held=fields, changed=changed,
                    merge_current=False, delivery=delivery,
                )  # fmt: skip
            # A merge's path REPLACES self_resolved: healing is credited to the
            # fix whenever the fix turns up. Any other path stands.
            self_resolved = case.row["resolution_path"] == ResolutionPath.SELF_RESOLVED
            result = await self._apply(
                case,
                CaseEvent.MERGED,
                fields,
                at,
                detail=detail,
                sticky=("merge_sha", "resolution_path"),
                replace=("resolution_path",) if self_resolved else (),
                changed=changed,
                delivery=delivery,
            )
            if not had_merge and result["merged_at"] is not None and result["deploy_sha"] is None:
                result = await self._anchor_kept_deploy(case) or result
            return result

    async def record_deploy(
        self,
        case_id: str,
        *,
        sha: str,
        marker_at: datetime,
        services: list[str] | None = None,
        at: datetime | None = None,
        event_key: str | None = None,
    ) -> CaseResult:
        """Record a deploy: the deployed sha, the marker's own timestamp and
        the services deployed (``./lab deploy backend`` is a partial deploy).

        **The caller vouches that ``sha`` contains the case's merge** (a git
        ancestry check in the deployed checkout); the ledger has no git and
        only checks times. The anchor confirm-healed measures from is the
        EARLIEST deploy that is not older than the merge. It never moves
        forward: later deploys are history only.

        A deploy is never refused. One that cannot anchor — the case has no
        merge yet (the marker beat the merge webhook), or the deploy predates
        the merge — is kept as a history row, and ``record_merge`` picks its
        anchor from the kept ones when the merge lands.
        """
        _checked("at", at)
        _checked("marker_at", marker_at)
        delivery = _delivery(event_key, "record_deploy", sha=sha, marker_at=marker_at, services=services)
        async with self._locked(case_id) as found:
            case = _require(found, case_id)
            if (dup := await self._duplicate(case, delivery)) is not None:
                return dup
            deploy = {"deploy_sha": sha, "deploy_marker_at": marker_at, "deploy_services": services}
            # The whole deploy goes into the history whatever becomes of it:
            # a merge that lands later picks its anchor from these.
            reported = {"deploy": _json_safe(deploy)}
            merged_at, anchor_at = case.row["merged_at"], case.row["deploy_marker_at"]
            if merged_at is None or marker_at < merged_at:
                return await self._apply(
                    case,
                    CaseEvent.DEPLOYED,
                    {},
                    at or marker_at,
                    detail=reported,
                    held=deploy,
                    deploy_anchors=False,
                    delivery=delivery,
                )
            if anchor_at is not None and marker_at < anchor_at:
                # Delivered second, deployed first: this is the true anchor.
                # It replaces the old one whole (unknown services included),
                # and a verdict measured from the old anchor is void — a stale
                # `healed` is worse than `pending`, so confirm-healed re-measures.
                deploy["deployed_at"] = at or marker_at
                replaced = dict(reported)
                clears: tuple[str, ...] = () if services is not None else ("deploy_services",)
                if case.row["healed_verdict"] != Verdict.PENDING:
                    replaced["anchor_replaced_after_verdict"] = _json_safe(
                        {
                            "verdict": case.row["healed_verdict"],
                            "evidence": case.row["verdict_evidence"],
                            "checked_at": case.row["verdict_checked_at"],
                        }
                    )
                    deploy["healed_verdict"] = Verdict.PENDING.value
                    clears = (*clears, "verdict_evidence", "verdict_checked_at")
                return await self._apply(
                    case,
                    CaseEvent.DEPLOYED,
                    deploy,
                    at or marker_at,
                    detail=replaced,
                    replace=tuple(deploy),
                    clears=clears,
                    delivery=delivery,
                )
            return await self._apply(
                case,
                CaseEvent.DEPLOYED,
                deploy,
                at or marker_at,
                detail=reported,
                sticky=_DEPLOY_COLUMNS,
                delivery=delivery,
            )

    async def record_verdict(
        self,
        case_id: str,
        verdict: Verdict | str,
        *,
        evidence: dict[str, Any],
        checked_at: datetime | None = None,
        event_key: str | None = None,
    ) -> CaseResult:
        """Record a confirm-healed outcome. Needs no merge — a finding can
        heal by itself or outside the code. A later check may revise it.

        ``evidence`` is counts and queries, never raw log lines (it is served
        by the API); above ``MAX_EVIDENCE_BYTES`` it is refused.

        A ``healed`` verdict on a case nothing has resolved sets
        ``resolution_path='self_resolved'`` right away. A case that has a
        merge keeps the merge's path — healing is credited to the fix, and a
        merge that turns up later REPLACES ``self_resolved`` — so
        ``self_resolved`` always means "no merge". If the verdict is later
        revised away from ``healed``, a ``self_resolved`` path is withdrawn
        with it.
        """
        verdict = Verdict(verdict)
        if verdict is Verdict.PENDING:
            raise ValueError("'pending' is the absence of a verdict and cannot be recorded")
        if len(json.dumps(evidence, default=str)) > MAX_EVIDENCE_BYTES:
            raise ValueError(
                f"evidence exceeds {MAX_EVIDENCE_BYTES} bytes: record counts and queries, never raw log lines"
            )
        checked = checked_at or datetime.now(UTC)
        _checked("checked_at", checked_at)
        delivery = _delivery(event_key, "record_verdict", verdict=verdict.value, evidence=evidence)
        async with self._locked(case_id) as found:
            case = _require(found, case_id)
            if (dup := await self._duplicate(case, delivery)) is not None:
                return dup
            fields = {"healed_verdict": verdict.value, "verdict_evidence": evidence, "verdict_checked_at": checked}
            clears: tuple[str, ...] = ()
            path = case.row["resolution_path"]
            if verdict is Verdict.HEALED and path is None:
                fields["resolution_path"] = ResolutionPath.SELF_RESOLVED.value
            elif (
                verdict is not Verdict.HEALED and path == ResolutionPath.SELF_RESOLVED and case.row["merged_at"] is None
            ):
                clears = ("resolution_path",)
            return await self._apply(
                case,
                CaseEvent.VERDICT_RECORDED,
                fields,
                checked,
                volatile=("verdict_checked_at",),
                clears=clears,
                delivery=delivery,
            )

    async def record_recurrence(
        self, case_id: str, *, occurrences: int = 1, at: datetime | None = None, event_key: str | None = None
    ) -> CaseResult:
        """The case's signature fired again. Counts it and moves
        ``last_seen_at`` forward; never moves the case. Every call counts, so
        pass an ``event_key`` (the tick or alert id) if the source redelivers.

        On a CLOSED case the row is frozen, and a signature that keeps firing
        must not grow the history without bound: all post-close recurrences
        share ONE history row that carries the running count and first/last
        sighting. (The caller should be opening a regression case instead.)
        That count is INFORMATIONAL: the row remembers only its last
        ``_POST_CLOSE_KEYS`` (50) event keys, so a redelivery is ignored only
        while fewer than 50 other keyed recurrences arrived in between;
        beyond that, or without a key, it counts again.
        """
        if occurrences < 1:
            raise ValueError("occurrences must be at least 1")
        when = at or datetime.now(UTC)
        _checked("at", at)
        delivery = _delivery(event_key, "record_recurrence", occurrences=occurrences)
        async with self._locked(case_id) as found:
            case = _require(found, case_id)
            if (dup := await self._duplicate(case, delivery)) is not None:
                return dup
            if transition(case.row["stage"], CaseEvent.RECURRENCE).effect is Effect.POST_CLOSE:
                return await self._note_post_close_recurrence(case, occurrences, when, event_key)
            last_seen = case.row["last_seen_at"]
            fields = {
                "occurrence_count": case.row["occurrence_count"] + occurrences,
                "last_seen_at": when if last_seen is None or when > last_seen else last_seen,
            }
            return await self._apply(
                case, CaseEvent.RECURRENCE, fields, at, detail={"occurrences": occurrences}, delivery=delivery
            )

    async def reopen(
        self, case_id: str, *, reason: str, at: datetime | None = None, event_key: str | None = None
    ) -> CaseResult:
        """Send a ``not_healed`` case back for a person to decide on another
        fix attempt.

        The case returns to ``tracked``, not ``accepted`` (#658). This write
        dispatches nothing, and ``accepted`` means a job should be starting:
        it is the stage the accepted-case dead-man alert watches. Accepting
        the case again dispatches the retry, and that job's link accepts it
        as the first one did.

        The row then describes the NEW attempt: verdict, resolution, merge
        and deploy anchor are all cleared, so nothing can judge the retry
        from the failed attempt's deploy (``fix_deployed`` is false until the
        retry's own deploy lands). ``accepted_at`` is cleared too, so no age
        is ever measured from the failed attempt's acceptance, and
        ``tracked_at`` becomes the reopen: the wait for the new decision
        starts here. The ticket and the issue stay linked. ``verified_at`` is
        kept: the startup backfill leaves a judged case alone, and would
        otherwise replay the failed attempt's job and re-accept the case on
        every restart. The failed attempt stays in the history and its PRs
        keep their rows. A regression after close is a new case
        (``open_case`` on the same ticket, or with ``parent_case_id``), not a
        reopen.
        """
        reason = _cap("reason", reason) or ""
        _checked("at", at)
        delivery = _delivery(event_key, "reopen", reason=reason)
        async with self._locked(case_id) as found:
            case = _require(found, case_id)
            if (dup := await self._duplicate(case, delivery)) is not None:
                return dup
            if case.row["stage"] == Stage.VERIFIED and case.row["healed_verdict"] != Verdict.NOT_HEALED:
                raise CaseConflictError(
                    f"case {case_id} has verdict {case.row['healed_verdict']!r}; only 'not_healed' can be reopened"
                )
            if transition(case.row["stage"], CaseEvent.REOPENED).effect is not Effect.APPLIED:
                return CaseResult(case.row, WriteOutcome.UNCHANGED)
            when = at or datetime.now(UTC)
            fields: dict[str, Any] = {"healed_verdict": Verdict.PENDING.value, "tracked_at": when}
            cleared = (
                "verdict_evidence",
                "verdict_checked_at",
                "resolution_path",
                "merge_sha",
                "merged_at",
                *_DEPLOY_COLUMNS,
                "deployed_at",
                "accepted_at",
            )
            return await self._apply(
                case, CaseEvent.REOPENED, fields, when, detail={"reason": reason}, clears=cleared, delivery=delivery
            )

    async def abandon(
        self,
        case_id: str,
        *,
        note: str | None = None,
        at: datetime | None = None,
        event_key: str | None = None,
    ) -> CaseResult:
        """The fix attempt was given up: close an ``accepted`` case as
        ``abandoned`` (#410).

        Only the person's own "abandon" raises this — see the stage machine
        for why a merely failed job must leave its case alone. The stage is
        the only guard: on a case with a PR, a merge or a verdict this is
        history and the case does not move, so a late or redelivered abandon
        can never close work that shipped.

        An accepted case carries no ``resolution_path`` — nothing has resolved
        it, and a retry after a ``not_healed`` verdict had its predecessor's
        cleared by ``reopen`` — so the path is simply written. Where the event
        does NOT apply the data is *held* instead: the case row is untouched
        and the history still records the attempt. Passing it as ordinary
        fields would write it anyway (a ``NOTED`` event writes what differs),
        stamping ``abandoned`` on a case whose PR is still open.
        """
        note = _cap("note", note)
        _checked("at", at)
        delivery = _delivery(event_key, "abandon", note=note)
        async with self._locked(case_id) as found:
            case = _require(found, case_id)
            if (dup := await self._duplicate(case, delivery)) is not None:
                return dup
            proposed: dict[str, Any] = {"resolution_path": ResolutionPath.ABANDONED.value}
            if note is not None:
                proposed["note"] = note
            if transition(case.row["stage"], CaseEvent.ABANDONED).effect is Effect.APPLIED:
                return await self._apply(case, CaseEvent.ABANDONED, proposed, at, delivery=delivery)
            return await self._apply(case, CaseEvent.ABANDONED, {}, at, delivery=delivery, held=proposed)

    async def close(
        self,
        case_id: str,
        *,
        resolution_path: ResolutionPath | str | None = None,
        note: str | None = None,
        at: datetime | None = None,
        event_key: str | None = None,
    ) -> CaseResult:
        """Close the case. ``resolution_path`` is required unless the case
        already has one (from a merge, or ``self_resolved`` from a healed
        verdict); a path the case already has is kept."""
        path = ResolutionPath(resolution_path) if resolution_path is not None else None
        note = _cap("note", note)
        _checked("at", at)
        delivery = _delivery(event_key, "close", resolution_path=path.value if path else None, note=note)
        async with self._locked(case_id) as found:
            case = _require(found, case_id)
            if (dup := await self._duplicate(case, delivery)) is not None:
                return dup
            if transition(case.row["stage"], CaseEvent.CLOSED).effect is not Effect.APPLIED:
                return CaseResult(case.row, WriteOutcome.UNCHANGED)
            resolved = case.row["resolution_path"] or (path.value if path else None)
            if resolved is None:
                raise ValueError(f"case {case_id} has no resolution_path yet; close() needs one")
            fields: dict[str, Any] = {"resolution_path": resolved}
            if note is not None:
                fields["note"] = note
            return await self._apply(case, CaseEvent.CLOSED, fields, at, delivery=delivery)

    # --------------------------------------------------------------- internals

    @asynccontextmanager
    async def _locked(self, case_id: str) -> AsyncIterator[LockedCase | None]:
        """``repo.locked`` plus the metrics of what was written under it.

        ``_apply`` runs inside the transaction; a counter moved there would
        also count a write that then rolled back. The events are collected
        and counted after the block — and with it the commit — succeeded.
        """
        pending: list[tuple[str, str, str | None]] = []
        token = _PENDING_METRICS.set(pending)
        try:
            async with self._repo.locked(case_id) as found:
                yield found
        finally:
            _PENDING_METRICS.reset(token)
        for event, effect, resolution_path in pending:
            self._metrics.case_event(event, effect)
            if event == CaseEvent.CLOSED.value and effect == Effect.APPLIED.value:
                self._metrics.case_resolved(resolution_path)

    async def _duplicate(self, case: LockedCase, delivery: _Delivery) -> CaseResult | None:
        """The result for a call whose event_key this case has already seen."""
        if delivery.event_key is None:
            return None
        seen = await case.event_by_key(delivery.event_key)
        if seen is None:
            return None
        if seen["detail"].get("request") == delivery.request:
            return CaseResult(case.row, WriteOutcome.DUPLICATE)
        logger.warning(
            "healing_case_event_key_conflict",
            case_id=case.case_id,
            event_key=delivery.event_key,
            first_event=seen["event"],
        )
        return CaseResult(case.row, WriteOutcome.DUPLICATE_CONFLICT)

    async def _merge_is_current(
        self, case: LockedCase, merge_sha: str | None, pr_already_merged: bool, when: datetime
    ) -> bool:
        """Whether a reported merge belongs to the case's CURRENT fix attempt.

        After a ``reopen`` the row has no merge, and the failed attempt's merge
        can be redelivered (a fresh delivery id, a replayed queue, a sweep).
        It is recognised by any of: its PR is already merged in the ledger;
        its sha is one reported before the reopen; its time is not later than
        the reopen. The sha and time checks hold even once the retry HAS a
        merge — otherwise a stale redelivery would fill a retry merge whose
        sha is still unknown. Before any reopen every merge is current.
        """
        reopen = await case.last_reopen()
        if reopen is None:
            return True
        if when <= reopen["occurred_at"]:
            return False
        if merge_sha is not None and merge_sha in await case.merge_shas_before(reopen["id"]):
            return False
        # While the retry has no merge yet, a PR the ledger already knows as
        # merged can only be the failed attempt's. (Once it has one, the
        # current merge's own redelivery looks exactly like this.)
        return not (case.row["merged_at"] is None and pr_already_merged)

    async def _owned_pr(self, case: LockedCase, url: str) -> dict[str, Any] | None:
        pr = await case.get_pr(url)
        if pr is not None and pr["case_id"] != case.case_id:
            raise CaseConflictError(f"PR {url} already belongs to case {pr['case_id']}")
        return pr

    async def _anchor_kept_deploy(self, case: LockedCase) -> CaseResult | None:
        """A merge just landed: anchor on the earliest deploy the history
        kept that is not older than it, if there is one."""
        merged_at = case.row["merged_at"]
        kept = []
        for deploy in await case.reported_deploys():
            marker_at = datetime.fromisoformat(deploy["deploy_marker_at"])
            if marker_at >= merged_at:
                kept.append((marker_at, deploy))
        if not kept:
            return None
        marker_at, held = min(kept, key=lambda pair: pair[0])
        deploy = {
            "deploy_sha": held["deploy_sha"],
            "deploy_marker_at": marker_at,
            "deploy_services": held.get("deploy_services"),
        }
        return await self._apply(
            case,
            CaseEvent.DEPLOYED,
            deploy,
            marker_at,
            detail={"anchored_from_history": True},
            sticky=_DEPLOY_COLUMNS,
            delivery=_Delivery(None, ""),
        )

    async def _note_post_close_recurrence(
        self, case: LockedCase, occurrences: int, when: datetime, event_key: str | None
    ) -> CaseResult:
        stamp = when.isoformat()
        noted = await case.post_close_recurrence()
        if noted is None:
            first: dict[str, Any] = {
                "updates": {},
                "effect": Effect.POST_CLOSE.value,
                "occurrences": occurrences,
                "first_seen_at": stamp,
                "last_seen_at": stamp,
                "recent_event_keys": [event_key] if event_key else [],
            }
            await case.add_event(CaseEvent.RECURRENCE.value, Stage.CLOSED.value, Stage.CLOSED.value, first, when)
            return CaseResult(case.row, WriteOutcome.APPLIED)
        body: dict[str, Any] = dict(noted["detail"])
        keys: list[str] = list(body.get("recent_event_keys", []))
        if event_key is not None and event_key in keys:
            return CaseResult(case.row, WriteOutcome.DUPLICATE)
        sightings: list[str] = [body.get("first_seen_at", stamp), body.get("last_seen_at", stamp), stamp]
        body["occurrences"] = int(body.get("occurrences", 0)) + occurrences
        body["first_seen_at"] = min(sightings, key=datetime.fromisoformat)
        body["last_seen_at"] = max(sightings, key=datetime.fromisoformat)
        body["recent_event_keys"] = ([*keys, event_key] if event_key else keys)[-_POST_CLOSE_KEYS:]
        await case.replace_event_detail(noted["id"], body)
        return CaseResult(case.row, WriteOutcome.APPLIED)

    async def _apply(
        self,
        case: LockedCase,
        event: CaseEvent,
        fields: dict[str, Any],
        at: datetime | None,
        *,
        delivery: _Delivery,
        detail: dict[str, Any] | None = None,
        sticky: tuple[str, ...] = (),
        volatile: tuple[str, ...] = (),
        clears: tuple[str, ...] = (),
        held: dict[str, Any] | None = None,
        replace: tuple[str, ...] = (),
        changed: bool = False,
        other_open_prs: bool = False,
        deploy_anchors: bool = True,
        merge_current: bool = True,
    ) -> CaseResult:
        """Resolve ``event`` against the locked case and write the outcome.

        ``fields`` are the call's data (None = not given). ``sticky`` columns
        are fill-only on a redelivery; ``replace`` names columns a call
        that knows better may overwrite even when late. ``volatile`` ones differ on every call (a check
        timestamp) and are written on a redelivery only alongside a real
        change. ``clears`` are set to NULL when the event lands. ``held`` is
        data the case must not take (a deploy that cannot anchor) but the
        history must keep. ``changed`` says the caller already changed a
        child row, which alone earns a history row.
        """
        row = case.row
        result = transition(
            row["stage"],
            event,
            other_open_prs=other_open_prs,
            deploy_anchors=deploy_anchors,
            merge_current=merge_current,
        )
        when = at or datetime.now(UTC)
        given = {k: v for k, v in fields.items() if v is not None}
        updates: dict[str, Any] = {}
        not_applied: dict[str, Any] = dict(held or {})
        wipes = {name: None for name in clears if row[name] is not None}

        if result.effect is Effect.APPLIED:
            updates = {**fields, **wipes, "stage": result.stage.value}
            stamp = f"{result.stage.value}_at"
            if not result.backward or row[stamp] is None:
                updates[stamp] = when
        elif result.effect is Effect.REPEAT:
            for name, value in given.items():
                if row[name] == value:
                    continue
                if name in sticky and row[name] is not None and name not in replace:
                    not_applied[name] = value
                else:
                    updates[name] = value
            updates.update(wipes)
            if not set(updates) - set(volatile):
                updates = {}
        elif result.effect is Effect.LATE:
            updates = {k: v for k, v in given.items() if row[k] is None or (k in replace and row[k] != v)}
            if replace:
                updates.update(wipes)
            if result.target is not None and row[f"{result.target.value}_at"] is None:
                updates.setdefault(f"{result.target.value}_at", when)
        elif result.effect is Effect.NOTED:
            updates = {k: v for k, v in given.items() if row[k] != v}
        else:  # POST_CLOSE: the case row is frozen
            not_applied.update(given)

        body: dict[str, Any] = {"updates": _json_safe(updates), **(detail or {}), "effect": result.effect.value}
        if not_applied:
            body["not_applied"] = _json_safe(not_applied)
        if not updates and not changed:
            # Nothing landed. Only a refused, held or post-close delivery is
            # still worth a row — once.
            if not not_applied or await case.event_recorded(event.value, body):
                return CaseResult(row, WriteOutcome.UNCHANGED)
        if delivery.event_key is not None:
            body["request"] = delivery.request

        # The cap counts drafts awaiting a human. Acceptance is that answer,
        # so the slot ends here — not when the case later closes (#415).
        if event is CaseEvent.ACCEPTED and result.effect in (Effect.APPLIED, Effect.REPEAT, Effect.LATE):
            await case.release_draft_slot(when)

        await case.update(updates)
        await case.add_event(event.value, row["stage"], result.stage.value, body, when, delivery.event_key)
        pending = _PENDING_METRICS.get()
        if pending is not None:
            pending.append((event.value, result.effect.value, updates.get("resolution_path") or row["resolution_path"]))
        log = logger.warning if not_applied and result.effect is Effect.REPEAT else logger.info
        log(
            "healing_case_event",
            case_id=case.case_id,
            case_event=event.value,
            from_stage=row["stage"],
            to_stage=result.stage.value,
            effect=result.effect.value,
            not_applied=sorted(not_applied) or None,
        )
        return CaseResult(case.row, WriteOutcome.APPLIED)


def _require(case: LockedCase | None, case_id: str) -> LockedCase:
    if case is None:
        raise CaseNotFoundError(case_id)
    return case


def _refuse_issue_swap(case: LockedCase, issue_id: str) -> None:
    held = case.row["issue_id"]
    if held is not None and held != issue_id:
        raise CaseConflictError(f"case {case.case_id} already links issue {held}; refusing to swap it for {issue_id}")
