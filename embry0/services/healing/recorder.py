"""CaseRecorder — how the pipeline's own code reports to the ledger (#278 part 2).

The watcher, the dispatch path and the job lifecycle each call ONE method
here at the moment something happens to a finding. Two rules hold for every
method:

* **Fail-open for the caller's own work.** Filing a ticket, dispatching a job
  and opening a PR are the primary actions; the ledger is their record. A
  ledger error is logged at ``error`` with the ids needed to repair the case
  by hand and the method returns ``None`` — it never raises, and it never
  swallows silently.
* **Every write is keyed.** ``event_key`` is derived from a stable id of the
  thing that happened (watcher run id, job id), so a retried call, a second
  code path reporting the same fact, or a restart cannot double-count.

Everything is written through :class:`HealingCaseService`; nothing here
touches the tables.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, cast

import structlog

from embry0.services.healing.lifecycle import Origin, ResolutionPath, Stage
from embry0.services.healing.service import CaseResult, HealingCaseService, TrackerRef
from embry0.storage.repo_renames import normalize_repo_url, repo_url_variants

logger = structlog.get_logger(__name__)

# Trackers whose dispatched tickets open a case when none exists, and the
# origin such a case gets. GitHub and GitLab joined Linear once the ledger's
# one-open-case-per-ticket index became (repo, tracker, tracker_key) and the
# lookups took a repo (#385): their issue numbers repeat across repos, so
# before that a case for issue #42 could have been any repo's.
_CASE_OPENING_TRACKERS: dict[str, Origin] = {
    "linear": Origin.HUMAN,
    "github": Origin.HUMAN,
    "gitlab": Origin.HUMAN,
}

_PR_NUMBER = re.compile(r"/(?:pull|merge_requests)/(\d+)/?$")

DEFERRED_NOTE = (
    "Detected while the watcher's draft cap was full: no ticket has been filed yet. "
    "The watcher files it, oldest first and at most one per tick, when a draft slot frees."
)
DEFERRED_EXPIRED_NOTE = (
    "Never filed: the finding waited for a draft slot until it was older than the watcher's "
    "proposal window, and was not seen again in that time."
)
DEFERRED_UNRECOVERABLE_NOTE = (
    "Never filed: the proposal text could not be read back from the finding's analysis job when a draft slot freed."
)
DRAFT_RELEASED_NOTE = "The draft ticket was {state} in the tracker before anyone accepted it."


async def record_case_event(
    owner: Any, method: str, /, *args: Any, log_ids: dict[str, Any] | None = None, **kwargs: Any
) -> None:
    """Call one ``CaseRecorder`` method from the pipeline, or do nothing.

    The boundary guard for the two call sites inside the job lifecycle. The
    recorder is an injected collaborator that older callers and tests leave
    unset, and it is not statically typed there, so this both tolerates its
    absence and refuses to let anything it does reach the caller: a dispatch
    and a finished job are never lost to their own bookkeeping.
    :class:`CaseRecorder` already swallows ledger errors — this catches the
    boundary itself (a wrong object, a future method that forgets to guard).
    """
    recorder = getattr(owner, "_case_recorder", None)
    if recorder is None:
        return
    try:
        await getattr(recorder, method)(*args, **kwargs)
    except Exception:
        logger.error("healing_case_boundary_failed", action=method, exc_info=True, **(log_ids or {}))


class CaseRecorder:
    def __init__(self, cases: HealingCaseService, *, pr_times: Any = None) -> None:
        self._cases = cases
        # #278: GitHubPrTimes — the forge's own open time for a PR. Optional;
        # without it a PR's opened_at falls back to a defensible bound and
        # says so, never to a wall clock passed off as the forge's.
        self._pr_times = pr_times

    # ---------------------------------------------------------------- watcher

    async def finding_recorded(
        self,
        *,
        repo: str,
        title: str,
        watcher_run_id: str,
        fingerprint: str | None,
        severity: str | None,
        tracker: TrackerRef | None,
        signature_id: str | None = None,
        signature: str | None = None,
        signature_source: str | None = None,
        parent_case_id: str | None = None,
        work_nature: dict[str, Any] | None = None,
    ) -> str | None:
        """The watcher analysed a window to a proposal. With ``tracker`` the
        draft ticket was filed (the case starts ``tracked``); without, the cap
        was full and the finding waits at ``detected``. Returns the case id.

        ``signature_id`` is #290's deterministic dedup key: the clustered log
        line the finding is about, stable across however the model happened to
        word the title. ``signature_source`` says whether that line is one
        the proposal's evidence quotes or only the window's loudest (#657).
        ``parent_case_id`` links a regression back to the closed case that
        carried the same signature. ``work_nature`` is the watcher's slopometer
        verdict (spec 2026-10-25, slop dungeon), None when it gave none.
        """

        async def write() -> CaseResult:
            return await self._cases.open_case(
                repo=repo,
                origin=Origin.WATCHER,
                work_nature=work_nature,
                title=title,
                fingerprint=fingerprint,
                signature_id=signature_id,
                signature=signature,
                signature_source=signature_source,
                parent_case_id=parent_case_id,
                severity=severity,
                watcher_run_id=watcher_run_id,
                tracker=tracker,
                note=None if tracker else DEFERRED_NOTE,
            )

        case = await self._guard(
            "finding_recorded", write, watcher_run_id=watcher_run_id, tracker_key=tracker.key if tracker else None
        )
        return str(case["id"]) if case is not None else None

    async def deferred_finding_filed(self, case_id: str, tracker: TrackerRef, *, watcher_run_id: str) -> bool:
        """A deferred finding got its draft ticket."""

        async def write() -> CaseResult:
            return await self._cases.attach_tracker(case_id, tracker, event_key=f"watcher-filed:{watcher_run_id}")

        return await self._guard("deferred_finding_filed", write, case_id=case_id, tracker_key=tracker.key) is not None

    async def deferred_finding_dropped(self, case_id: str, note: str) -> bool:
        """A deferred finding will never be filed (``note`` says why)."""

        async def write() -> CaseResult:
            return await self._cases.close(
                case_id,
                resolution_path=ResolutionPath.CANCELLED,
                note=note,
                event_key=f"watcher-deferred-dropped:{case_id}",
            )

        return await self._guard("deferred_finding_dropped", write, case_id=case_id) is not None

    async def finding_recurred(
        self,
        *,
        fingerprint: str | None = None,
        signature_id: str | None = None,
        occurrences: int = 1,
        repo: str | None,
        watcher_run_id: str,
    ) -> str | None:
        """The analysis proposed a finding the watcher already holds (a
        ``skipped_duplicate`` tick): count it on that finding's case.

        ``signature_id`` is tried first and is what #290 passes — it is derived
        from the log line, so it matches a rewording where the title hash does
        not. ``fingerprint`` remains the fallback for cases opened before
        signatures were recorded, which have no ``signature_id`` at all.

        ``occurrences`` is this window's count for the signature, so the case's
        ``occurrence_count`` tracks how loud the fault actually is rather than
        how many ticks happened to notice it.

        Returns the case id it counted against, or ``None``.
        """

        async def write() -> CaseResult | None:
            case = None
            if signature_id:
                case = await self._cases.find_by_signature(signature_id, repo=repo, origin=Origin.WATCHER)
            if case is None and fingerprint:
                case = await self._cases.find_by_fingerprint(fingerprint, origin=Origin.WATCHER, repo=repo)
            if case is None:
                logger.warning(
                    "healing_recurrence_without_case",
                    fingerprint=fingerprint,
                    signature_id=signature_id,
                    watcher_run_id=watcher_run_id,
                )
                return None
            return await self._cases.record_recurrence(
                case["id"], occurrences=occurrences, event_key=f"watcher-run:{watcher_run_id}"
            )

        result = await self._guard(
            "finding_recurred",
            write,
            fingerprint=fingerprint,
            signature_id=signature_id,
            watcher_run_id=watcher_run_id,
        )
        return str(result["id"]) if result is not None else None

    async def draft_released(self, tracker: TrackerRef, *, state: str, repo: str | None = None) -> None:
        """Reconcile found the draft cancelled in the tracker: nobody will
        accept it, so its case is closed as ``cancelled``. A case someone did
        accept (or that a merge resolved) is left alone.

        ``repo`` disambiguates GitHub/GitLab issue numbers, which repeat
        across repos; Linear identifiers are unique without it."""

        async def write() -> CaseResult | None:
            case = await self._cases.find_by_tracker(tracker.kind, tracker.key, repo)
            if case is None or case["stage"] not in (Stage.DETECTED, Stage.TRACKED):
                return None
            return await self._cases.close(
                case["id"],
                resolution_path=ResolutionPath.CANCELLED,
                note=DRAFT_RELEASED_NOTE.format(state=state),
                event_key=f"watcher-draft-released:{tracker.key}",
            )

        await self._guard("draft_released", write, tracker_key=tracker.key, state=state)

    # ------------------------------------------------------- dispatch and jobs

    async def job_dispatched(self, issue: dict[str, Any], job_id: str) -> str | None:
        """A pipeline job was created for ``issue`` (first dispatch, a retry
        after a failed job, a re-triage). Links it to the issue's case, which
        accepts the case; returns the case id.

        The case is looked up by issue, then by the issue's tracker ticket —
        that is how a watcher draft someone labelled is recognised. A ticket
        nobody opened a case for (a human wrote it) gets one here, provided
        its tracker is in ``_CASE_OPENING_TRACKERS``.
        """
        issue_id = str(issue.get("id") or "")
        tracker, key = issue.get("tracker"), issue.get("tracker_key")
        case_repo = str(issue.get("repo") or "") or None

        async def write() -> CaseResult | None:
            case = await self._cases.find_by_issue(issue_id) if issue_id else None
            if (case is None or case["stage"] == Stage.CLOSED) and tracker and key:
                origin = _CASE_OPENING_TRACKERS.get(str(tracker))
                by_ticket = await self._cases.find_by_tracker(str(tracker), str(key), case_repo)
                if by_ticket is not None and by_ticket["stage"] != Stage.CLOSED:
                    case = by_ticket
                elif origin is not None:
                    # No open case: a ticket a human wrote, or one whose
                    # earlier case is closed (open_case links the predecessor).
                    case = await self._cases.open_case(
                        repo=str(issue.get("repo") or "unknown/unknown"),
                        origin=origin,
                        title=str(issue.get("title") or ""),
                        tracker=TrackerRef(str(tracker), str(key), issue.get("tracker_url")),
                    )
                else:
                    case = case or by_ticket
            if case is None:
                return None
            return await self._cases.attach_job(case["id"], job_id, event_key=f"job:{job_id}")

        case = await self._guard("job_dispatched", write, issue_id=issue_id, job_id=job_id, tracker_key=key)
        return str(case["id"]) if case is not None else None

    async def pr_followup_dispatched(self, *, pr_url: str, job_id: str) -> str | None:
        """A follow-up job was dispatched from a comment on a PR (#344). It
        carries the PR but no issue, so ``job_dispatched`` never sees it;
        link it to the case that owns that PR — whichever job attached the PR
        first — so the case's job list holds the review-feedback work too.
        A PR no case owns is a no-op. Returns the case id.
        """
        url = normalize_repo_url(pr_url)

        async def write() -> CaseResult | None:
            case = None
            for variant in repo_url_variants(pr_url):
                case = await self._cases.find_by_pr_url(variant)
                if case is not None:
                    break
            if case is None:
                logger.debug("healing_case_none_for_pr_followup", pr_url=url, job_id=job_id)
                return None
            return await self._cases.attach_job(case["id"], job_id, event_key=f"job:{job_id}")

        case = await self._guard("pr_followup_dispatched", write, job_id=job_id, pr_url=url)
        return str(case["id"]) if case is not None else None

    async def job_abandoned(self, *, issue_id: str | None, job_id: str, note: str | None = None) -> str | None:
        """A person chose "abandon" at the retry pause and the job produced no
        PR (#410). Closes the issue's case as ``abandoned``; returns the case id.

        Without this the acceptance had no terminal state: the case stayed at
        ``accepted`` with no job running and nothing could move it, so
        ``Embry0HealingAcceptedCaseNotDispatched`` paged forever on a decision
        a human had already made. Reported for that one choice only — a job
        that failed some other way leaves its case open on purpose, because
        the work was accepted and has not been delivered.
        """

        async def write() -> CaseResult | None:
            case = await self._cases.find_by_issue(issue_id) if issue_id else None
            if case is None:
                return None
            return await self._cases.abandon(case["id"], note=note, event_key=f"job-abandoned:{job_id}")

        case = await self._guard("job_abandoned", write, issue_id=issue_id, job_id=job_id)
        return str(case["id"]) if case is not None else None

    async def pr_opened(
        self, *, issue_id: str | None, job_id: str, pr_url: str, job_started_at: datetime | None = None
    ) -> str | None:
        """The job ended with a change request (the developer's, or the
        orchestrator's fallback). Recorded on the case that already owns that
        PR, else on the issue's case; returns the case id.

        ``opened_at`` is asked of the forge, because later readers use it to
        judge whether an event is stale: a time stamped when the JOB ended
        would be later than the real one and would make a genuine forge event
        look out of date. When the forge cannot say, ``job_started_at`` is
        used — the job was running before it opened the PR, so that bound is
        never later than the truth — and the gap is logged.
        """
        url = normalize_repo_url(pr_url)

        async def write() -> CaseResult | None:
            return await self._attach_pr(
                issue_id,
                pr_url,
                owner_job_id=job_id,
                opened_at=lambda: self._pr_opened_at(url, job_id, job_started_at),
                event_key=f"job-pr:{job_id}",
            )

        case = await self._guard("pr_opened", write, issue_id=issue_id, job_id=job_id, pr_url=url)
        return str(case["id"]) if case is not None else None

    async def existing_pr_found(self, *, issue_id: str | None, job_id: str, pr_url: str) -> str | None:
        """A job found that an open PR it did not open already covers its
        ticket, and ended skipped (#474). The PR is the case's — it is what
        will fix the finding — but it has NO owning job, so its merge
        resolves the case ``out_of_band``: a person (or another system) did
        that work. Returns the case id.

        Keyed on the skipping job, so a retried call adds nothing; a later
        ``pr_opened`` for the same PR by a job that really opened it still
        claims the row (``attach_pr`` fills a missing ``job_id``).
        """
        url = normalize_repo_url(pr_url)

        async def opened_at() -> datetime | None:
            # The forge's open time when it can say; otherwise None ("now"):
            # this job started after the PR was opened, so it bounds nothing.
            return cast("datetime | None", await self._pr_times.opened_at(url)) if self._pr_times is not None else None

        async def write() -> CaseResult | None:
            return await self._attach_pr(
                issue_id, pr_url, owner_job_id=None, opened_at=opened_at, event_key=f"existing-pr:{job_id}"
            )

        case = await self._guard("existing_pr_found", write, issue_id=issue_id, job_id=job_id, pr_url=url)
        return str(case["id"]) if case is not None else None

    async def _attach_pr(
        self,
        issue_id: str | None,
        pr_url: str,
        *,
        owner_job_id: str | None,
        opened_at: Callable[[], Awaitable[datetime | None]],
        event_key: str,
    ) -> CaseResult | None:
        """Attach a PR to the case that already owns it, else the issue's case.

        The PR is matched across every spelling of a renamed repo: migration
        65 rewrote the URL columns that existed then, so the same PR can be
        stored under the old slug here and the new one elsewhere, and a
        single-spelling lookup would add a second row for it.
        """
        case, stored_url = None, None
        for variant in repo_url_variants(pr_url):
            found = await self._cases.find_by_pr_url(variant)
            if found is not None:
                case, stored_url = found, variant
                break
        if case is None and issue_id:
            case = await self._cases.find_by_issue(issue_id)
        if case is None:
            return None
        # Keep the PR under the spelling the ledger already knows it by;
        # a new row gets the repo's current one.
        known_url = stored_url or normalize_repo_url(pr_url)
        match = _PR_NUMBER.search(known_url)
        return await self._cases.attach_pr(
            case["id"],
            url=known_url,
            number=int(match.group(1)) if match else None,
            job_id=owner_job_id,
            # Asked only once a case is found: no forge call for a PR no case wants.
            at=await opened_at(),
            event_key=event_key,
        )

    async def case_origin(self, issue_id: str) -> str | None:
        """The origin of the case this issue belongs to (``watcher``,
        ``human``, …), or None — no case, or the ledger could not say. A read,
        and fail-open like every method here: the caller only uses it to
        tighten what a job runs (#306)."""
        try:
            case = await self._cases.find_by_issue(issue_id)
        except Exception:
            logger.error("healing_case_read_failed", action="case_origin", issue_id=issue_id, exc_info=True)
            return None
        return str(case["origin"]) if case and case.get("origin") else None

    async def _pr_opened_at(self, url: str, job_id: str, job_started_at: datetime | None) -> datetime | None:
        """The forge's open time for this PR, else the best defensible bound."""
        opened: datetime | None = None
        if self._pr_times is not None:
            opened = cast("datetime | None", await self._pr_times.opened_at(url))
        if opened is not None:
            return opened
        logger.warning(
            "healing_pr_open_time_not_from_forge",
            pr_url=url,
            job_id=job_id,
            fallback="job_start" if job_started_at is not None else "record_time",
            detail="the forge's open time for this PR could not be read; opened_at is a bound, not the "
            "forge's own timestamp, and readers that judge staleness by it should allow for that.",
        )
        return job_started_at

    # --------------------------------------------------------------- internals

    async def _guard(
        self, action: str, write: Callable[[], Awaitable[CaseResult | None]], **ids: Any
    ) -> CaseResult | None:
        try:
            result = await write()
        except Exception:
            logger.error("healing_case_write_failed", action=action, exc_info=True, **ids)
            return None
        if result is not None and getattr(result, "outcome", None) == "duplicate_conflict":
            logger.error("healing_case_write_conflict", action=action, case_id=result.get("id"), **ids)
        return result
