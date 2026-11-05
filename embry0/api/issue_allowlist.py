"""A key's repository allowlist applied to issues: which issues, and which agents' questions, a caller reads and acts on.

An issue belongs to its repository (``issues.repo``). A key with an
allowlist reads an issue only when its list covers that repository. Any
other issue, one with no repository among them, answers such a key exactly
as an issue that does not exist:

- a route that reads one issue, or something of it (its activity, its
  questions), reads it with the key's repositories
  (``IssuesRepository.get(repos=...)``), finds nothing, and answers what it
  answers for an issue that does not exist;
- the list of issues leaves such issues out in its query, before it pages
  and counts; an issue's children and jobs (``GET /issues/{id}``), and every
  issue's counts of them, leave out those of repositories the key does not
  cover (a job's repository is
  :func:`embry0.storage.repositories.jobs.job_repository`);
- a route that acts on one such issue answers as for an issue that does not
  exist, and logs and audits the refusal
  (:func:`embry0.api.repo_allowlist.hide_unless_covered` with an action);
- deleting an issue the key reads is refused when it would also cancel a
  child issue, or a job, of a repository the key does not cover
  (:func:`embry0.api.repo_allowlist.enforce_reach`): the answer names the
  issue's own repository and says why, never the other one.

An agent's question (an ``issue_inputs`` row) is asked by a job about an
issue, so it belongs to both. A key with an allowlist reads or answers it
only when it covers the job's repository and, when the question names an
issue, the issue's. A job's questions and an issue's leave the others out
in their query (``IssueInputsRepository.list_by_job`` and ``list_by_issue``
with ``repos``), and answering one answers as a question that does not exist
(:func:`hide_input_unless_covered`), logged and audited.

What links an issue the key reads, or a job, to an issue it does not read
(work that crosses repositories, as when an issue moves after its job ran or
its parent moves) is shown and followed only so far as the key reads it:

- a job names no such issue: its ``issue_id`` is null to the key
  (:func:`without_hidden_issues`), and an issue names no such parent: its
  ``parent_issue_id`` is null (:func:`without_hidden_parents`);
- the key's change of an issue's status does not roll up into such a parent
  (:func:`parent_hidden`): the parent's status is not recalculated, and
  nothing is refused, since a refusal would tell the parent exists;
- an action of the key never makes a new link to such an issue: running a
  job again starts the new job with no issue (:func:`hidden_issue_ids`). An
  action on an existing job (resume, cancel) keeps the link someone allowed
  to make it made, and its answer names no issue.

A key with an allowlist cannot create an issue with no repository
(``POST /issues`` refuses it), so it never reads one. Sessions, the master
key, AUTH_DEV_MODE and keys without an allowlist read every issue, as before,
with no lookup made for the rule.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any

from fastapi import HTTPException, Request

from embry0.api.repo_allowlist import covered_slugs, hide_unless_covered, key_covers


async def input_repositories(request: Request, inp: Mapping[str, Any]) -> list[str | None]:
    """The repositories the question ``inp`` belongs to: its job's and, when it names an issue, the issue's. None
    for one with no repository."""
    job_id = str(inp.get("job_id") or "")
    jobs = (await request.app.state.jobs_repo.repositories_of([job_id])) if job_id else {}
    out: list[str | None] = [jobs.get(job_id)]
    issue_id = inp.get("issue_id")
    if issue_id:
        out.append((await request.app.state.issues_repo.repositories_of([str(issue_id)])).get(str(issue_id)))
    return out


async def hide_input_unless_covered(
    request: Request, inp: Mapping[str, Any], missing: HTTPException, *, action: str | None = None
) -> None:
    """Raise ``missing``, the route's answer for a question that does not exist, when the caller's key does not
    cover each repository the question belongs to (:func:`input_repositories`). An ``action`` is logged and
    audited as refused, naming the first repository it does not cover; a read is not. No lookup is made for
    sessions, the master key, AUTH_DEV_MODE and keys without an allowlist."""
    if covered_slugs(request) is None:
        return
    for repo in await input_repositories(request, inp):
        await hide_unless_covered(request, repo, missing, action=action)


async def hidden_issue_ids(request: Request, issue_ids: Iterable[str | None]) -> set[str]:
    """The ids among ``issue_ids`` that name an issue the caller's key does not read: of a repository it does not
    cover, of none, or no issue at all. Empty, with nothing looked up, for sessions, the master key, AUTH_DEV_MODE
    and keys without an allowlist."""
    wanted = {str(issue_id) for issue_id in issue_ids if issue_id}
    if covered_slugs(request) is None or not wanted:
        return set()
    found = await request.app.state.issues_repo.repositories_of(wanted)
    return {issue_id for issue_id in wanted if not key_covers(request, found.get(issue_id))}


async def _without_hidden(request: Request, rows: Iterable[MutableMapping[str, Any]], field: str) -> None:
    """Null ``field`` in each of ``rows`` that names an issue the caller's key does not read, in one lookup."""
    rows = list(rows)
    hidden = await hidden_issue_ids(request, (row.get(field) for row in rows))
    for row in rows:
        if row.get(field) and str(row[field]) in hidden:
            row[field] = None


async def without_hidden_issues(request: Request, jobs: Iterable[MutableMapping[str, Any]]) -> None:
    """Null the ``issue_id`` of each job in ``jobs`` that names an issue the caller's key does not read, so a job the
    key reads names no issue of a repository it does not cover. Nothing is looked up for sessions, the master key,
    AUTH_DEV_MODE and keys without an allowlist."""
    await _without_hidden(request, jobs, "issue_id")


async def without_hidden_parents(request: Request, issues: Iterable[MutableMapping[str, Any]]) -> None:
    """Null the ``parent_issue_id`` of each issue in ``issues`` whose parent the caller's key does not read, so an
    issue the key reads names no parent of a repository it does not cover. As :func:`without_hidden_issues`."""
    await _without_hidden(request, issues, "parent_issue_id")


async def parent_hidden(request: Request, issue: Mapping[str, Any]) -> bool:
    """Whether ``issue`` has a parent the caller's key does not read, so a change the key makes to ``issue`` must not
    roll up into it. False, with nothing looked up, for an issue with no parent and for sessions, the master key,
    AUTH_DEV_MODE and keys without an allowlist."""
    parent = issue.get("parent_issue_id")
    return bool(parent) and str(parent) in await hidden_issue_ids(request, [str(parent)])
