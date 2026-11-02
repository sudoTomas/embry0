"""A key's repository allowlist applied to jobs: which jobs a caller reads and acts on.

A job belongs to the repository it works on
(:func:`embry0.storage.repositories.jobs.job_repository`): its ``repo``, else
the repository its git context names. A key with an allowlist reads a job
only when its list covers that repository. Any other job, one with no
repository among them, answers such a key exactly as a job that does not
exist:

- a route that reads one job, or something of it (its events, inputs,
  deliverables, verification, QA results and artifacts), answers with what
  it answers for a job that does not exist (:func:`job_hidden`);
- a list of jobs, or a count of them, leaves such jobs out in its query,
  before it pages, limits and counts (``covered_slugs``);
- a token minted for such a job's stream opens a stream that streams nothing,
  as the stream of a job that does not exist does
  (:func:`stream_scope`, ``scoped_tokens.reads_nothing``);
- a route that acts on one such job answers as for a job that does not
  exist, and logs and audits the refusal (:func:`hide_job_unless_covered`).

A key with an allowlist cannot start a job with no repository
(``POST /jobs`` refuses it), so it never reads one. Sessions, the master key,
AUTH_DEV_MODE and keys without an allowlist read every job, as before, with
no lookup made for the rule.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request

from embry0.api.repo_allowlist import covered_slugs, hide_unless_covered, key_covers
from embry0.api.scoped_tokens import reads_nothing
from embry0.storage.repositories.jobs import job_repository


def artifact_job(job_id: str) -> str:
    """The job a QA artifact id belongs to: ``job_id`` itself, or for a QA sub-task (``<job_id>__<app>``), its job.

    A sub-task has no row of its own; its artifacts are stored under its id."""
    return job_id.split("__", 1)[0]


async def job_hidden(request: Request, job_id: str) -> bool:
    """Whether ``job_id`` answers the caller as a job that does not exist: a key with an allowlist that does not
    cover the job's repository, or a job that does not exist. Always False, with no lookup, for sessions, the
    master key, AUTH_DEV_MODE and keys without an allowlist. Logs and audits nothing: a read."""
    if covered_slugs(request) is None:
        return False
    jobs = getattr(request.app.state, "jobs_repo", None)
    found = await jobs.repositories_of([job_id]) if jobs is not None else {}
    return not key_covers(request, found.get(job_id))


async def hide_job_unless_covered(
    request: Request, job: Mapping[str, Any], missing: HTTPException, *, action: str | None = None
) -> None:
    """Raise ``missing``, the route's answer for a job that does not exist, when the caller's key does not cover
    ``job``'s repository. An ``action`` is logged and audited as refused; a read is not
    (:func:`embry0.api.repo_allowlist.hide_unless_covered`)."""
    await hide_unless_covered(request, job_repository(job), missing, action=action)


async def stream_scope(request: Request, scope: str, job_id: str) -> str:
    """The scope a token for ``job_id``'s stream is signed for: ``scope``, or, for a job the caller does not read
    (:func:`job_hidden`), the scope of a stream that reads nothing. The token looks the same either way."""
    return reads_nothing(scope) if await job_hidden(request, job_id) else scope
