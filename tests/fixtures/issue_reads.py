"""Two repositories' issues, with everything the issue routes read, on the real app over a real database.

The app, its keys and its work are :mod:`tests.fixtures.job_reads`': one open
issue in :data:`COVERED`, the repository the limited key covers, and one in
:data:`UNCOVERED` (:data:`ISSUE`), each with its repository's finished job
linked to it, an agent's question asked by that job, and a merge approval of
that job. :func:`issue_reads_app` gives each issue what the issue routes read
besides its row: a body and a label, a child issue, its activity (audit rows
naming it), and a question carrying its :data:`MARK`.

It also plants the work that crosses between the two repositories, as when an
issue moves to another repository after its job ran (``PUT /issues/{id}`` with
``repo``, or a tracker re-routing it):

- :data:`MIXED` in the covered repository, with a child issue
  (:data:`CHILD_ELSEWHERE`), a job (:data:`JOB_ELSEWHERE`) and that job's
  question (:data:`INPUT_ELSEWHERE`) in the uncovered one;
- :data:`MIXED_HIDDEN` in the uncovered repository, with a job of the covered
  one (:data:`JOB_HERE`), that job's question (:data:`INPUT_HERE`), a paused
  job of the covered one (:data:`PAUSED_HERE`), and a child issue of the
  covered one (:data:`CHILD_HERE`).

Everything stored for an issue of the uncovered repository, or for its work,
carries its :data:`MARK`, and each merge approval's ask its
:data:`APPROVAL_MARK`, so an answer that shows any of it is found
(:func:`shows_uncovered_issue`, :func:`shows_uncovered_approval`).
:data:`MISSING` is an issue id that names nothing and is as long as the
uncovered issue's (:data:`MISSING_INPUT` and :data:`MISSING_APPROVAL` the
same for a question and an approval), so two answers compare byte for byte
once each id is put back as a placeholder (:func:`same_answer`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from embry0.api.middleware.rate_limit import RateLimitMiddleware
from embry0.storage.database import DatabasePool
from tests.fixtures.job_reads import job_reads_app
from tests.fixtures.repository_writes import COVERED, NOW, TAG, UNCOVERED, Keys, ids

#: Each repository's open issue.
ISSUE = {repo: ids(repo)["issue_id"] for repo in (COVERED, UNCOVERED)}
#: Each issue's question, asked by its repository's job.
INPUT = {repo: ids(repo)["input_id"] for repo in (COVERED, UNCOVERED)}
#: Each issue's child issue, in its own repository.
CHILD = {repo: f"iss-kid-{TAG[repo]}" for repo in (COVERED, UNCOVERED)}
#: An issue id that names no issue, as long as the uncovered repository's.
MISSING = "iss-zzzzz"
#: A question id that names no question, as long as the uncovered repository's.
MISSING_INPUT = "in-zzzzz"
#: What everything stored for each repository's issues carries.
MARK = {COVERED: "widget-issue-content", UNCOVERED: "gizmo-issue-content"}
#: Each repository's merge approval, of its finished job, and what its ask carries.
APPROVAL = {repo: ids(repo)["approval_id"] for repo in (COVERED, UNCOVERED)}
APPROVAL_MARK = {COVERED: "widget-approval-content", UNCOVERED: "gizmo-approval-content"}
#: A merge approval id that names none, as long as the uncovered repository's.
MISSING_APPROVAL = "ma-zzzzz"

#: An issue of the covered repository whose child, job and question are the uncovered repository's.
MIXED = "iss-mixed-widget"
CHILD_ELSEWHERE = "iss-kid-elsewhere"
JOB_ELSEWHERE = "job-mixed-gizmo"
INPUT_ELSEWHERE = "in-mixed-gizmo"
#: An issue of the uncovered repository whose job and question are the covered repository's.
MIXED_HIDDEN = "iss-mixed-gizmo"
JOB_HERE = "job-mixed-widget"
INPUT_HERE = "in-mixed-widget"
CHILD_HERE = "iss-kid-here"
PAUSED_HERE = "job-paused-widget"

assert len(MISSING) == len(ISSUE[UNCOVERED]), "the missing id is as long as the uncovered issue's"
assert len(MISSING_INPUT) == len(INPUT[UNCOVERED]), "the missing question id is as long as the uncovered one's"
assert len(MISSING_APPROVAL) == len(APPROVAL[UNCOVERED]), "the missing approval id is as long as the uncovered one's"


async def _issue(
    db: DatabasePool, issue_id: str, repo: str, title: str, *, parent: str | None = None, hours: float = 6
) -> None:
    await db.execute(
        "INSERT INTO issues (id, title, body, labels, repo, status, parent_issue_id, created_at) "
        "VALUES ($1, $2, $3, $4::jsonb, $5, 'open', $6, $7)",
        issue_id,
        title,
        title,
        [title],
        repo,
        parent,
        NOW - timedelta(hours=hours),
    )


async def _job(
    db: DatabasePool, job_id: str, repo: str, issue_id: str, task: str, status: str = "awaiting_input"
) -> None:
    """A job of ``repo`` on ``issue_id``, waiting on its question, started by the person the issue's asks go to."""
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, issue_id, created_at, started_at, "
        "dispatched_by) VALUES ($1, $2, $3, $4, 'full-magnum-opus', $5, $6, $6, 'ada@example.com')",
        job_id,
        repo,
        task,
        status,
        issue_id,
        NOW - timedelta(hours=3),
    )


async def _input(db: DatabasePool, input_id: str, issue_id: str, job_id: str, question: str) -> None:
    await db.execute(
        "INSERT INTO issue_inputs (id, issue_id, job_id, question) VALUES ($1, $2, $3, $4)",
        input_id,
        issue_id,
        job_id,
        question,
    )


async def _activity(db: DatabasePool, issue_id: str, note: str) -> None:
    await db.execute(
        "INSERT INTO audit_log (action, actor, details, issue_id) VALUES ('issue.updated', 'ada@example.com', $1, $2)",
        {"note": note},
        issue_id,
    )


async def seed_issues(db: DatabasePool) -> None:
    """Each repository's issue given what the issue routes read, and the work that crosses the two."""
    for repo in (COVERED, UNCOVERED):
        mark = MARK[repo]
        await db.execute(
            "UPDATE issues SET body = $2, labels = $3::jsonb, tracker = 'github', tracker_key = '12', "
            "tracker_url = $4 WHERE id = $1",
            ISSUE[repo],
            f"body of {mark}",
            [mark],
            f"https://github.com/{repo}/issues/12",
        )
        await db.execute("UPDATE issue_inputs SET question = $2 WHERE id = $1", INPUT[repo], f"asks {mark}?")
        await _issue(db, CHILD[repo], repo, f"child of {mark}", parent=ISSUE[repo], hours=4)
        await _activity(db, ISSUE[repo], f"activity of {mark}")
        await db.execute(
            "UPDATE merge_approvals SET ask = $2::jsonb WHERE id = $1", APPROVAL[repo], {"note": APPROVAL_MARK[repo]}
        )
    hidden = MARK[UNCOVERED]
    await _issue(db, MIXED, COVERED, f"mixed {MARK[COVERED]}")
    await _issue(db, CHILD_ELSEWHERE, UNCOVERED, f"child elsewhere {hidden}", parent=MIXED, hours=4)
    await _job(db, JOB_ELSEWHERE, UNCOVERED, MIXED, f"job elsewhere {hidden}")
    await _input(db, INPUT_ELSEWHERE, MIXED, JOB_ELSEWHERE, f"asked elsewhere {hidden}?")
    await _issue(db, MIXED_HIDDEN, UNCOVERED, f"mixed hidden {hidden}")
    await _activity(db, MIXED_HIDDEN, f"activity of {hidden}")
    await _job(db, JOB_HERE, COVERED, MIXED_HIDDEN, f"job here {MARK[COVERED]}")
    await _input(db, INPUT_HERE, MIXED_HIDDEN, JOB_HERE, f"asked here about {hidden}?")
    await _issue(db, CHILD_HERE, COVERED, f"child here {MARK[COVERED]}", parent=MIXED_HIDDEN, hours=3.5)
    await _job(db, PAUSED_HERE, COVERED, MIXED_HIDDEN, f"paused here {MARK[COVERED]}", status="paused")


@asynccontextmanager
async def issue_reads_app(
    db: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[FastAPI, Keys, Mapping[str, Mapping[str, str]]]]:
    """:func:`tests.fixtures.job_reads.job_reads_app`, each issue given what the issue routes read."""
    # A walk of every route reads far more than one key's minute of requests; the limiter is not what is read here.
    monkeypatch.setattr(RateLimitMiddleware, "_over_limit", lambda _self, _key, _max_rpm: False)
    async with job_reads_app(db, monkeypatch) as (app, keys, values):
        await seed_issues(db)
        yield app, keys, values


def shows_uncovered_issue(text: str) -> bool:
    """Whether an answer shows an issue of the uncovered repository, or any of its work: its id, or anything stored
    for it."""
    return any(needle in text for needle in (ISSUE[UNCOVERED], MIXED_HIDDEN, CHILD_ELSEWHERE, MARK[UNCOVERED]))


def shows_uncovered_approval(text: str) -> bool:
    """Whether an answer shows the uncovered repository's merge approval: its id, or what its ask carries."""
    return APPROVAL[UNCOVERED] in text or APPROVAL_MARK[UNCOVERED] in text


def placeholders(text: bytes, names: Mapping[str, str]) -> bytes:
    """``text`` with each name in ``names`` put back as its placeholder, longest first."""
    for name in sorted(names, key=len, reverse=True):
        text = text.replace(name.encode(), names[name].encode())
    return text


def same_answer(
    a: httpx.Response, a_names: Mapping[str, str], b: httpx.Response, b_names: Mapping[str, str]
) -> tuple[Any, Any] | None:
    """None when ``a`` and ``b`` are the same answer, status, headers and body, once each one's ids are put back as
    their placeholders; else both, for the message."""

    def shown(response: httpx.Response, names: Mapping[str, str]) -> tuple[int, list[tuple[str, str]], bytes]:
        headers = sorted((k.lower(), placeholders(v.encode(), names).decode()) for k, v in response.headers.items())
        return response.status_code, headers, placeholders(response.content, names)

    left, right = shown(a, a_names), shown(b, b_names)
    return None if left == right else (left, right)
