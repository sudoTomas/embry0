"""The area mapper proposes, a person applies (spec 2026-11-10, ADR 0019).

A map of a repository's areas no longer opens a pull request. Its job's
report carries a document; the executor stores it on the map's proposal
(:func:`finish_map`), and a person applies it as the repository's areas or
rejects it. An apply is a compare-and-set against the document the map
started from, so a change made meanwhile is never overwritten; it is a
revision (source ``proposal``) that undo reverses. This module is that
life cycle: finishing a map, reading the newest one for its card (ending one
whose job stopped without it), applying and rejecting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Literal

import structlog

from embry0.services.areas_document import (
    DocumentInvalid,
    DocumentTooLarge,
    changes,
    check,
    load_yaml,
    problem_list,
    text_diff,
)
from embry0.services.areas_store import AreasStore
from embry0.storage.repositories.jobs import TERMINAL_JOB_STATUSES
from embry0.storage.repositories.repo_areas import AreasChanged
from embry0.storage.repositories.repo_areas_proposals import RepoAreasProposalsRepository

logger = structlog.get_logger(__name__)

STALE: Final = "Someone changed this repository's areas after the map started. Map again for a fresh one."
ALREADY_RUNNING: Final = "embry0 is already mapping this repository's areas."
NOT_FOUND: Final = "That map was not found for this repository."
NO_DOCUMENT: Final = "The map's report carries no areas document, so there is nothing to apply."

# Why a proposal that is not waiting for a person cannot be applied or rejected.
_NOT_WAITING: Final[dict[str, str]] = {
    "running": "embry0 is still mapping this repository's areas.",
    "applied": "These areas were already applied.",
    "rejected": "These areas were already rejected.",
    "stale": STALE,
    "failed": "embry0 could not finish this map.",
    "unchanged": "This map found the areas embry0 already keeps: there is nothing to apply.",
}


class Refused(Exception):
    """A step said no. ``status_code`` and ``detail`` are the HTTP answer."""

    def __init__(self, status_code: int, detail: str | list[dict[str, Any]]) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _checked(text: str) -> dict[str, Any]:
    """``text``'s values; :class:`DocumentInvalid` or :class:`DocumentTooLarge` when it cannot be stored."""
    return check(load_yaml(text))


async def finish_map(
    store: AreasStore,
    proposals: RepoAreasProposalsRepository,
    proposal_id: str,
    *,
    job_id: str,
    repo: str,
    document: str | None,
    notes: str,
    failure: str | None,
) -> str | None:
    """Store how a map ended on its proposal; the status it ended in, or None when it had ended already, or is
    not this job's.

    A job finishes only its own repository's map: the proposal must name
    ``job_id`` as its job and ``repo`` (the job's repository) as its
    repository, so no other job, whatever its ``pipeline_config`` says,
    stores a document on it. ``failure`` (the job failed) ends it
    ``failed``. A report with no document, or one the schema refuses, ends
    it ``failed`` naming why (each problem by key); a document whose values
    equal the stored ones ends it ``unchanged``; any other waits for a
    person (``pending``).
    """
    proposal = await proposals.get(proposal_id)
    if proposal is None:
        return None
    if proposal["job_id"] != job_id or str(proposal["repo"]).lower() != repo.lower():
        logger.warning("areas_map_not_this_jobs", proposal_id=proposal_id, job_id=job_id)
        return None
    status: Literal["pending", "failed", "unchanged"]
    kwargs: dict[str, str]
    if failure is not None:
        status, kwargs = "failed", {"failure": f"embry0 stopped before it finished: {failure}"[:2000]}
    elif document is None:
        status, kwargs = "failed", {"failure": NO_DOCUMENT}
    else:
        try:
            data = _checked(document)
        except (DocumentInvalid, DocumentTooLarge) as exc:
            listed = "; ".join(f"{'.'.join(map(str, p['loc'])) or 'document'}: {p['msg']}" for p in problem_list(exc))
            status, kwargs = "failed", {"failure": f"The map's document cannot be stored: {listed}"[:2000]}
        else:
            stored = await store.get(proposal["repo"])
            if stored.stored and stored.data == data:
                status, kwargs = "unchanged", {}
            else:
                status, kwargs = "pending", {}
    finished = await proposals.finish(
        proposal_id,
        status=status,
        proposed_text=document,
        notes_md=notes,
        **kwargs,
    )
    logger.info("areas_map_finished", proposal_id=proposal_id, status=status, recorded=finished)
    return status if finished else None


async def settle(app_state: Any, proposal: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The proposal and its job. A ``running`` proposal whose job has ended is finished as failed first: nothing
    else would end it, and it would hold the repository."""
    job_id = proposal.get("job_id")
    job = await app_state.jobs_repo.get(job_id) if job_id else None
    if proposal["status"] == "running" and job is not None and job["status"] in TERMINAL_JOB_STATUSES:
        reason = str(job.get("error_message") or "").strip()
        failure = f"embry0 stopped before it finished: {reason}" if reason else "embry0 stopped before it finished."
        proposals = app_state.repo_areas_proposals_repo
        await proposals.finish(proposal["id"], status="failed", failure=failure[:2000])
        proposal = await proposals.get(proposal["id"]) or proposal
    return proposal, job


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def proposal_view(app_state: Any, repo: str) -> dict[str, Any] | None:
    """The repository's newest map as its card reads it, or None: what applying it would change, area by area,
    the owners it adds or removes first, and the text diff."""
    latest = await app_state.repo_areas_proposals_repo.latest(repo)
    if latest is None:
        return None
    proposal, job = await settle(app_state, latest)
    stored = await app_state.areas.get(repo)
    proposed = proposal["proposed_text"]
    found: list[dict[str, Any]] = []
    diff = ""
    if proposed is not None:
        try:
            found = changes(stored.data, _checked(proposed))
        except (DocumentInvalid, DocumentTooLarge):
            found = []
        diff = text_diff(stored.yaml_text, proposed)
    return {
        "id": proposal["id"],
        "repo": proposal["repo"],
        "status": proposal["status"],
        "job_id": proposal["job_id"],
        "job_status": job["status"] if job is not None else None,
        "base_generation": int(proposal["base_generation"]),
        "stale": proposal["status"] == "pending" and int(proposal["base_generation"]) != stored.generation,
        "proposed_text": proposed,
        "changes": found,
        "text_diff": diff,
        "notes_md": proposal["notes_md"],
        "failure": proposal["failure"],
        "created_by": proposal["created_by"],
        "created_at": _iso(proposal["created_at"]),
        "decided_by": proposal["decided_by"],
        "decided_at": _iso(proposal["decided_at"]),
    }


async def _waiting(app_state: Any, repo: str, proposal_id: str) -> dict[str, Any]:
    proposal: dict[str, Any] | None = await app_state.repo_areas_proposals_repo.get(proposal_id)
    if proposal is None or proposal["repo"] != repo:
        raise Refused(404, NOT_FOUND)
    if proposal["status"] != "pending":
        raise Refused(409, _NOT_WAITING[proposal["status"]])
    return proposal


async def _decided_meanwhile(proposals: Any, proposal_id: str) -> Refused:
    """The refusal for a proposal someone else decided after ``_waiting`` read it."""
    now = await proposals.get(proposal_id)
    return Refused(409, _NOT_WAITING[now["status"]] if now is not None else NOT_FOUND)


async def apply_proposal(app_state: Any, repo: str, proposal_id: str, *, actor: str) -> int | None:
    """Store a waiting map as ``repo``'s areas and return the revision id (None when the text equals the stored
    one). Refused 409 when the areas changed since the map started (the map is then ``stale``), 422 when its
    document is no longer valid."""
    proposals: RepoAreasProposalsRepository = app_state.repo_areas_proposals_repo
    store: AreasStore = app_state.areas
    proposal = await _waiting(app_state, repo, proposal_id)
    base = int(proposal["base_generation"])
    if (await store.get(repo)).generation != base:
        await proposals.decide(proposal_id, status="stale", decided_by=actor)
        raise Refused(409, STALE)
    text = str(proposal["proposed_text"] or "")
    try:
        data = _checked(text)
    except (DocumentInvalid, DocumentTooLarge) as exc:
        raise Refused(422, problem_list(exc)) from None
    # The decision and the write commit together, and only while the proposal is still pending and the areas
    # are still the ones the map started from, so a rejection or a save that landed meanwhile wins.
    applied = False
    revision_id: int | None = None
    try:
        async with app_state.db.transaction() as conn:
            applied = await proposals.decide(proposal_id, status="applied", decided_by=actor, conn=conn)
            if applied:
                _, revision_id = await store.write_on(
                    conn,
                    repo,
                    text,
                    data,
                    expected={base},
                    actor=actor,
                    source="proposal",
                    reason=f"map {proposal_id} (job {proposal['job_id']})",
                )
    except AreasChanged:
        await proposals.decide(proposal_id, status="stale", decided_by=actor)
        raise Refused(409, STALE) from None
    if not applied:
        raise await _decided_meanwhile(proposals, proposal_id)
    return revision_id


async def reject_proposal(app_state: Any, repo: str, proposal_id: str, *, actor: str) -> None:
    proposals = app_state.repo_areas_proposals_repo
    await _waiting(app_state, repo, proposal_id)
    if not await proposals.decide(proposal_id, status="rejected", decided_by=actor):
        raise await _decided_meanwhile(proposals, proposal_id)
