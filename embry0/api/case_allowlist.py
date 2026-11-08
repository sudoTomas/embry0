"""A key's repository allowlist applied to healing cases: which cases, and what of them, a caller reads.

A case belongs to the repository it is filed on (``healing_cases.repo``): a
watcher finding's stack, the repository a signal names or is mapped to (an
alert's labels, a CI run), the repository of the ticket a person or a webhook
opened it from, and an organization need's one repository. A key with an
allowlist reads a case only when its list covers that repository. Any other
case answers such a key exactly as a case that does not exist:

- a route that reads one case reads it with the key's repositories
  (``HealingCasesRepository.get_detail(repos=...)``), finds nothing, and
  answers what it answers for a case that does not exist;
- the list of cases leaves such cases out in its query, before it pages,
  counts and makes its cursor, with every filter (``list_page(repos=...)``);
- a route that acts on one such case answers as for a case that does not
  exist, and logs and audits the refusal
  (:func:`embry0.api.repo_allowlist.answer_as_missing` with an action).

A case filed on no repository is no such key's either, as a job or an issue
with no repository is not: a case filed on nothing, and the case an
organization need covering several repositories, or none, files on its
owner. Such a case's title and note are the need's words as they were stored,
counted over every repository the need names, so they cannot be narrowed to a
list the way ``GET /orgs/{owner}/needs`` narrows the need itself; the key
reads the need there, narrowed, and dismissing it there closes the case.

What a case the key reads links to in another repository is shown only so far
as the key reads it. Work crosses repositories when an issue moves after its
job ran, or a pull request elsewhere names the case's ticket:

- the case lists, and counts, only the jobs that work on a repository the key
  covers, and the pull requests in one (a pull request is in the repository
  its address names, ``pr_repository``); a pull request whose job the key does
  not read names no job (``job_id`` null);
- its ``issue_id``, ``parent_case_id`` and ``caused_regression_case_id`` are
  null when they name an issue or a case the key does not read, and its
  ``merge_sha`` when it is the merge of a pull request the key does not read
  (:func:`without_hidden_links`), as is a need's ``case_id``
  (:func:`without_hidden_cases`). Its stage, its stage times and whether a
  fix is deployed are the case's own and stay;
- its history leaves out every event that names, in its detail or in its key,
  a job, a pull request, an issue or a case the key does not read
  (:func:`narrow_detail`).

Nothing was refused, so a read logs and audits nothing. Sessions, the master
key, AUTH_DEV_MODE and keys without an allowlist read every case and all of
it, as before, with no lookup made for the rule.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException, Request

from embry0.api.issue_allowlist import hidden_issue_ids
from embry0.api.repo_allowlist import answer_as_missing, covered_slugs, key_covers
from embry0.services.healing.lifecycle import fix_deployed
from embry0.storage.repositories.healing_cases import pr_repository

#: The fields of a case that name another case.
CASE_LINKS = ("parent_case_id", "caused_regression_case_id")
#: An event key that names a job: the recorder's keys for a job linked, a job's pull request, a pull request a job
#: found open, and an attempt abandoned.
_JOB_KEY = re.compile(r"(?:job|job-pr|job-abandoned|existing-pr):(?P<job>.+)")
#: A pull request's address anywhere in an event's detail or key, as ``pr_repository`` reads one.
_PR_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"']+?/(?:pull|-/merge_requests)/[0-9]+")


def _wanted(ids: Iterable[str | None]) -> set[str]:
    return {str(item) for item in ids if item}


async def hidden_case_ids(request: Request, case_ids: Iterable[str | None]) -> set[str]:
    """The ids among ``case_ids`` that name a case the caller's key does not read: of a repository it does not cover,
    of none, or no case at all. Empty, with nothing looked up, for sessions, the master key, AUTH_DEV_MODE and keys
    without an allowlist."""
    wanted = _wanted(case_ids)
    if covered_slugs(request) is None or not wanted:
        return set()
    found = await request.app.state.healing_cases_repo.repositories_of(wanted)
    return {case_id for case_id in wanted if not key_covers(request, found.get(case_id))}


async def hidden_job_ids(request: Request, job_ids: Iterable[str | None]) -> set[str]:
    """The ids among ``job_ids`` that name a job the caller's key does not read, as :func:`hidden_case_ids` does for a
    case (a job's repository is :func:`~embry0.storage.repositories.jobs.job_repository`'s)."""
    wanted = _wanted(job_ids)
    if covered_slugs(request) is None or not wanted:
        return set()
    found = await request.app.state.jobs_repo.repositories_of(wanted)
    return {job_id for job_id in wanted if not key_covers(request, found.get(job_id))}


def pr_hidden(request: Request, url: str | None) -> bool:
    """Whether the pull request at ``url`` is in a repository the caller's key does not cover, or its address names
    none. Always False for sessions, the master key, AUTH_DEV_MODE and keys without an allowlist."""
    return covered_slugs(request) is not None and not key_covers(request, pr_repository(url))


async def without_hidden_cases(request: Request, rows: Iterable[MutableMapping[str, Any]], *fields: str) -> None:
    """Null each of ``fields`` (``case_id`` by default) in each of ``rows`` that names a case the caller's key does
    not read, in one lookup. Nothing is looked up for sessions, the master key, AUTH_DEV_MODE and keys without an
    allowlist."""
    rows, fields = list(rows), fields or ("case_id",)
    hidden = await hidden_case_ids(request, (row.get(name) for row in rows for name in fields))
    for row in rows:
        for name in fields:
            if row.get(name) and str(row[name]) in hidden:
                row[name] = None


async def without_hidden_links(request: Request, cases: Iterable[MutableMapping[str, Any]]) -> None:
    """Null the ``issue_id`` of each of ``cases`` that names an issue the caller's key does not read, and its
    ``parent_case_id`` and ``caused_regression_case_id`` that name such a case: one lookup each. Null its
    ``merge_sha`` when it is the merge of a pull request the key does not read (``merge_elsewhere``, read with the
    key's repositories), keeping ``fix_deployed`` as it was: the case's stage and its stage times are its own and
    stay. Nothing is looked up for sessions, the master key, AUTH_DEV_MODE and keys without an allowlist."""
    rows = list(cases)
    for row in rows:
        if row.pop("merge_elsewhere", False):
            row["fix_deployed"] = fix_deployed(
                merge_sha=row["merge_sha"], merged_at=row["merged_at"], deployed_at=row["deployed_at"]
            )
            row["merge_sha"] = None
    if covered_slugs(request) is None or not rows:
        return
    hidden = await hidden_issue_ids(request, (row.get("issue_id") for row in rows))
    for row in rows:
        if row.get("issue_id") and str(row["issue_id"]) in hidden:
            row["issue_id"] = None
    await without_hidden_cases(request, rows, *CASE_LINKS)


@dataclass
class _Named:
    """What one event of a case's history names."""

    jobs: set[str] = field(default_factory=set)
    issues: set[str] = field(default_factory=set)
    cases: set[str] = field(default_factory=set)
    prs: set[str] = field(default_factory=set)


def _named(event: Mapping[str, Any]) -> _Named:
    """The jobs, issues and cases an event's detail names under their fields anywhere in it (``job_id``,
    ``issue_id``, ``parent_case_id``, ``caused_regression_case_id``), the job its key names, and every pull request
    address in either."""
    named = _Named()
    by_field = {"job_id": named.jobs, "issue_id": named.issues, **{name: named.cases for name in CASE_LINKS}}

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for name, item in value.items():
                if isinstance(item, str) and item and name in by_field:
                    by_field[name].add(item)
                walk(item)
        elif isinstance(value, list | tuple):
            for item in value:
                walk(item)

    detail = event.get("detail") or {}
    walk(detail)
    key = str(event.get("event_key") or "")
    match = _JOB_KEY.fullmatch(key.strip())
    if match:
        named.jobs.add(match["job"])
    named.prs.update(_PR_URL.findall(f"{json.dumps(detail, default=str)} {key}"))
    return named


async def narrow_detail(request: Request, case: MutableMapping[str, Any]) -> None:
    """``case``, as ``get_detail(repos=...)`` read it for the caller's key, named only so far as the key reads what it
    links to: its links (:func:`without_hidden_links`), its pull requests' jobs, and its history, which leaves out
    every event that names a job, a pull request, an issue or a case the key does not read. Its jobs and pull
    requests were narrowed in the query. Nothing is looked up for sessions, the master key, AUTH_DEV_MODE and keys
    without an allowlist."""
    if covered_slugs(request) is None:
        return
    await without_hidden_links(request, [case])
    events = list(case.get("events") or [])
    named = [_named(event) for event in events]
    prs = list(case.get("prs") or [])
    jobs = await hidden_job_ids(request, [*(pr.get("job_id") for pr in prs), *(j for n in named for j in n.jobs)])
    issues = await hidden_issue_ids(request, (i for n in named for i in n.issues))
    cases = await hidden_case_ids(request, (c for n in named for c in n.cases))
    for pr in prs:
        if pr.get("job_id") and str(pr["job_id"]) in jobs:
            pr["job_id"] = None
    case["events"] = [
        event
        for event, n in zip(events, named, strict=True)
        if not (n.jobs & jobs or n.issues & issues or n.cases & cases or any(pr_hidden(request, u) for u in n.prs))
    ]


async def hide_case_unless_covered(
    request: Request, case: Mapping[str, Any], missing: HTTPException, *, action: str | None = None
) -> None:
    """Raise ``missing``, the route's answer for a case that does not exist, when the caller's key does not read
    ``case``: one of a repository it does not cover, or filed on an owner or on nothing. An ``action`` is logged and
    audited as refused, naming the case's repository, its owner's ``<owner>/*`` or ``(no repository)``; a read is
    not. No-op for sessions, the master key, AUTH_DEV_MODE and keys without an allowlist."""
    filed_on = str(case.get("repo") or "")
    if key_covers(request, filed_on):
        return
    shown = f"{filed_on}/*" if filed_on and "/" not in filed_on else (filed_on or "(no repository)")
    await answer_as_missing(request, shown, missing, action=action)
