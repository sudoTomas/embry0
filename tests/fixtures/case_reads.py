"""Two repositories' healing cases, with everything the case routes read, on the real app over a real database.

The app, its keys and its work are :mod:`tests.fixtures.issue_reads`': the
limited key covers :data:`COVERED` and not :data:`UNCOVERED`, and each holds
a watcher case waiting for a person (:data:`CASE`) linked to its
repository's finished job. :func:`case_reads_app` gives each case what the
case routes read besides its row: a note, a signature and a slop verdict, its
issue, a pull request opened by its job, a deploy, a verdict's evidence and
its history, each carrying its repository's :data:`MARK`, and an audit row of
it being looked at by a person.

It also plants the cases no key with an allowlist reads, though none is
another repository's: a case an organization need filed on each owner
(:data:`OWNER_CASE`, a need covering several repositories files its case on
the owner), linked from the covered owner's need, and a case with no
repository at all (:data:`NO_REPOSITORY`). Each repository's merge approval
names the other repository's case, as the covered repository's job would
when it works a case whose issue moved there.

And a case of the covered repository whose links cross into the other
(:data:`CROSSING`), as when its issue moves after its job ran: its issue
(:data:`tests.fixtures.issue_reads.MIXED_HIDDEN`), a job
(:data:`tests.fixtures.issue_reads.JOB_ELSEWHERE`), the pull request that job
opened (:data:`HIDDEN_PR`, merged, so a view that picks the merged one picks
it; its merge is the case's :data:`MERGE_SHA`, deployed), a pull request no
job opened (:data:`JOBLESS_PR`), a pull request of the covered repository
whose job is the other's (:data:`PR_OF_A_HIDDEN_JOB`), its parent case (the
other repository's) and the case it caused a regression in (one of no
repository), each named in its history (:data:`HIDDEN_EVENTS`), as are the
other repository's job in an event's key alone, its merge request on GitLab
and a pull request's address that names no repository; beside its own job
(:data:`tests.fixtures.issue_reads.JOB_HERE`), its own pull request
(:data:`OWN_PR`) and history that names only them (:data:`SHOWN_EVENTS`).
Another case of the covered repository (:data:`UNNAMED_MERGE`) merged
through a pull request whose address names no repository.

Everything stored for a case no key with an allowlist reads carries one of
:data:`HIDDEN_MARKS`, so an answer that shows any of it is found
(:func:`shows_hidden_case`); :func:`shows_crossing` also finds what the
crossing case links to elsewhere. :func:`missing_like` names, for each case
id, one that names nothing and is as long, so two answers compare byte for
byte once each id is put back as a placeholder
(:func:`tests.fixtures.issue_reads.same_answer`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from embry0.storage.database import DatabasePool
from tests.fixtures.issue_reads import APPROVAL, ISSUE, JOB_ELSEWHERE, JOB_HERE, MIXED_HIDDEN, issue_reads_app
from tests.fixtures.job_reads import JOB
from tests.fixtures.repository_writes import COVERED, NOW, OWNER, UNCOVERED, Keys, ids

#: Each repository's case: a watcher finding with a draft ticket, waiting for a person.
CASE = {repo: ids(repo)["case_id"] for repo in (COVERED, UNCOVERED)}
#: What everything stored for each repository's case carries.
MARK = {COVERED: "widget-case-content", UNCOVERED: "gizmo-case-content"}
#: Each repository's case's pull request, opened by its job.
PR = {repo: f"https://github.com/{repo}/pull/91" for repo in (COVERED, UNCOVERED)}

#: The case an organization need filed on each owner, and what it carries.
OWNER_CASE = {owner: f"hc-org-{owner}" for owner in OWNER.values()}
OWNER_MARK = "ownerwide-case-content"
#: The need of the covered owner whose case is filed on the owner: it covers the covered repository and one nobody
#: registered, so the key reads it narrowed to its repository.
OWNER_NEED = "need-org-acme"
#: A case with no repository, and what it carries.
NO_REPOSITORY = "hc-norepo"
NO_REPOSITORY_MARK = "norepo-case-content"

#: The covered repository's case whose links cross into the other repository.
CROSSING = "hc-cross-widget"
#: The crossing case's pull requests: its own job's, the other repository's job's (merged), one no job opened in the
#: other repository, and one of the covered repository whose job is the other's.
OWN_PR = f"https://github.com/{COVERED}/pull/94"
HIDDEN_PR = f"https://github.com/{UNCOVERED}/pull/92"
JOBLESS_PR = f"https://github.com/{UNCOVERED}/pull/93"
PR_OF_A_HIDDEN_JOB = f"https://github.com/{COVERED}/pull/95"
#: The other repository's merge request on GitLab, and a pull request's address that names no repository: the history
#: names each.
GITLAB_MR = f"https://gitlab.example.com/{UNCOVERED}/-/merge_requests/97"
NO_REPOSITORY_PR = "https://github.com/pull/98"
#: The merge of the crossing case, the other repository's pull request's, and when it merged.
MERGE_SHA = "feedface" * 5
MERGED_AT = NOW - timedelta(hours=1)
#: A case of the covered repository whose merge only a pull request whose address names no repository carries.
UNNAMED_MERGE = "hc-unnamed-widget"
UNNAMED_MERGE_SHA = "deadbeef" * 5
UNNAMED_PR = "https://github.com/pull/99"
#: The crossing case's history: what names something of the other repository, each marked, and what does not.
HIDDEN_EVENTS = {
    "hidden-event-job": "the other repository's job linked, with its issue",
    "hidden-event-pr": "the other repository's job's pull request opened",
    "hidden-event-jobless-pr": "a pull request of the other repository no job opened",
    "hidden-event-merge": "the other repository's pull request merged",
    "hidden-event-parent": "opened as a regression of the other repository's case",
    "hidden-event-key": "a pull request of its own, found by the other repository's job (in the event's key alone)",
    "hidden-event-pr-key": "the other repository's pull request closed, named in the event's key alone",
    "hidden-event-issue": "its issue linked, the other repository's, and nothing else",
    "hidden-event-abandoned": "an attempt of the other repository's job abandoned, named in the event's key alone",
    "hidden-event-job-pr-key": "its own pull request, reported for the other repository's job in the event's key alone",
    "hidden-event-regression": "named as having caused a regression, a case of no repository",
    "hidden-event-gitlab": "a merge request of the other repository on GitLab",
    "hidden-event-no-repository-pr": "a pull request whose address names no repository",
}
SHOWN_EVENTS = {
    "shown-event-job": "its own job linked",
    "shown-event-pr": "its own job's pull request opened",
    "shown-event-note": "a note",
}

#: What everything stored for a case no key with an allowlist reads carries.
HIDDEN_MARKS = (MARK[UNCOVERED], OWNER_MARK, NO_REPOSITORY_MARK)


def missing_like(case_id: str) -> str:
    """A case id that names no case and is as long as ``case_id``."""
    return "hc-" + "z" * (len(case_id) - 3)


async def _case(
    db: DatabasePool,
    case_id: str,
    repo: str,
    title: str,
    *,
    origin: str = "watcher",
    stage: str = "detected",
    hours: float = 3,
    **columns: Any,
) -> None:
    at = NOW - timedelta(hours=hours)
    row = {"id": case_id, "repo": repo, "title": title, "origin": origin, "stage": stage, **columns}
    row.update(created_at=at, detected_at=at)
    await db.execute(
        f"INSERT INTO healing_cases ({', '.join(row)}) VALUES ({', '.join(f'${i}' for i in range(1, len(row) + 1))})",
        *row.values(),
    )


async def _pr(
    db: DatabasePool, case_id: str, url: str, job_id: str | None, state: str = "open", merge_sha: str | None = None
) -> None:
    await db.execute(
        "INSERT INTO healing_case_prs (case_id, url, number, state, job_id, opened_at, merged_at, merge_sha) "
        "VALUES ($1, $2, $3, $4, $5, $6, CASE WHEN $4 = 'merged' THEN $6::timestamptz END, $7)",
        case_id,
        url,
        int(url.rsplit("/", 1)[1]),
        state,
        job_id,
        NOW - timedelta(hours=1),
        merge_sha,
    )


async def _event(
    db: DatabasePool, case_id: str, event: str, detail: Mapping[str, Any], key: str | None = None, *, to: str = ""
) -> None:
    await db.execute(
        "INSERT INTO healing_case_events (case_id, event, event_key, from_stage, to_stage, detail, occurred_at) "
        "VALUES ($1, $2, $3, NULL, $4, $5::jsonb, $6)",
        case_id,
        event,
        key,
        to or "pr_open",
        dict(detail),
        NOW - timedelta(minutes=30),
    )


async def seed_cases(db: DatabasePool) -> None:
    """Each repository's case given what the case routes read, the cases no restricted key reads, and the crossing."""
    for repo in (COVERED, UNCOVERED):
        mark, case_id = MARK[repo], CASE[repo]
        await db.execute(
            "UPDATE healing_cases SET title = $2, note = $3, signature = $4, signature_id = $5, severity = 'high', "
            "fingerprint = $6, work_nature = $7::jsonb, issue_id = $8, verdict_evidence = $9::jsonb WHERE id = $1",
            case_id,
            f"Case of {mark}",
            f"note of {mark}",
            f'{{job="{mark}"}} |= "boom"',
            f"sig-{mark}",
            f"fp-{mark}",
            {"nature": "bug", "slop_score": 10, "kinds": [], "evidence": [mark]},
            ISSUE[repo],
            {"query": mark},
        )
        await _pr(db, case_id, PR[repo], JOB[repo])
        await _event(db, case_id, "noted", {"updates": {"note": f"note of {mark}"}, "effect": "noted"}, to="tracked")
        await db.execute(
            "INSERT INTO audit_log (action, actor, details) VALUES ('case_closed', 'ada@example.com', $1)",
            {"case_id": case_id, "repo": repo, "note": mark},
        )
        # An ask about the decision that offers the case: its repository is read back from the case.
        await db.execute(
            "INSERT INTO asks (id, decision_id, recipient, due_at, created_at) "
            "VALUES ($1, $2, 'ada@example.com', $3, $3)",
            f"ask-{case_id}",
            f"offer:case:{case_id}",
            NOW - timedelta(minutes=10),
        )

    for owner, case_id in OWNER_CASE.items():
        await _case(db, case_id, owner, f"Add the webhook to 2 repositories {OWNER_MARK}", origin="org")
        await db.execute("UPDATE healing_cases SET note = $2 WHERE id = $1", case_id, f"detail {OWNER_MARK}")
    await db.execute(
        "INSERT INTO org_needs (id, owner, kind, signature, title, detail, repos, status, case_id) "
        "VALUES ($1, $2, 'no_webhook', 'sig-org', $3, $4, $5::jsonb, 'open', $6)",
        OWNER_NEED,
        OWNER[COVERED],
        f"Add the webhook to 2 repositories {OWNER_MARK}",
        f"detail {OWNER_MARK}",
        [COVERED, f"{OWNER[COVERED]}/unlisted"],
        OWNER_CASE[OWNER[COVERED]],
    )
    await _case(db, NO_REPOSITORY, "", f"An alert of no repository {NO_REPOSITORY_MARK}", origin="alert")
    # Each repository's merge approval names a case: the other repository's for the covered one's, as when the
    # covered repository's job works a case whose issue moved there.
    for repo, case_id in ((COVERED, CASE[UNCOVERED]), (UNCOVERED, CASE[UNCOVERED])):
        await db.execute("UPDATE merge_approvals SET case_id = $2 WHERE id = $1", APPROVAL[repo], case_id)

    await _crossing(db)
    await _case(
        db,
        UNNAMED_MERGE,
        COVERED,
        f"Merged {MARK[COVERED]}",
        stage="merged",
        hours=2,
        merge_sha=UNNAMED_MERGE_SHA,
        merged_at=MERGED_AT,
    )
    await _pr(db, UNNAMED_MERGE, UNNAMED_PR, None, state="merged", merge_sha=UNNAMED_MERGE_SHA)


async def _crossing(db: DatabasePool) -> None:
    """:data:`CROSSING` and what it links to, here and in the other repository."""
    covered = MARK[COVERED]
    await _case(
        db,
        CROSSING,
        COVERED,
        f"Crossing {covered}",
        stage="deployed",
        hours=2.5,
        issue_id=MIXED_HIDDEN,
        parent_case_id=CASE[UNCOVERED],
        caused_regression_case_id=NO_REPOSITORY,
        merge_sha=MERGE_SHA,
        merged_at=MERGED_AT,
        deploy_sha="def456",
        deploy_marker_at=NOW - timedelta(minutes=50),
        deployed_at=NOW - timedelta(minutes=50),
        tracker="github",
        tracker_key="96",
        tracker_url=f"https://github.com/{COVERED}/issues/96",
    )
    for job in (JOB_HERE, JOB_ELSEWHERE):
        await db.execute("INSERT INTO healing_case_jobs (case_id, job_id) VALUES ($1, $2)", CROSSING, job)
    await _pr(db, CROSSING, OWN_PR, JOB_HERE)
    await _pr(db, CROSSING, HIDDEN_PR, JOB_ELSEWHERE, state="merged", merge_sha=MERGE_SHA)
    await _pr(db, CROSSING, JOBLESS_PR, None, state="closed")
    await _pr(db, CROSSING, PR_OF_A_HIDDEN_JOB, JOB_ELSEWHERE, state="closed")
    applied = {"effect": "applied"}
    await _event(
        db,
        CROSSING,
        "opened",
        {"updates": {"repo": COVERED, "parent_case_id": CASE[UNCOVERED]}, **applied, "why": "hidden-event-parent"},
        to="tracked",
    )
    await _event(
        db,
        CROSSING,
        "accepted",
        {"updates": {"issue_id": MIXED_HIDDEN}, "job_id": JOB_ELSEWHERE, **applied, "why": "hidden-event-job"},
        f"job:{JOB_ELSEWHERE}",
        to="accepted",
    )
    await _event(
        db,
        CROSSING,
        "accepted",
        {"updates": {"issue_id": MIXED_HIDDEN}, "effect": "repeat", "why": "hidden-event-issue"},
    )
    await _event(db, CROSSING, "accepted", {"job_id": JOB_HERE, "effect": "repeat", "why": "shown-event-job"})
    await _event(
        db,
        CROSSING,
        "pr_opened",
        {"pr_url": HIDDEN_PR, "pr": {"state": "open", "job_id": JOB_ELSEWHERE}, **applied, "why": "hidden-event-pr"},
        "github:delivery-1",
    )
    await _event(
        db,
        CROSSING,
        "pr_opened",
        {"pr_url": OWN_PR, "pr": {"state": "open"}, "effect": "repeat", "why": "shown-event-pr"},
        f"job-pr:{JOB_HERE}",
    )
    await _event(
        db,
        CROSSING,
        "pr_opened",
        {"pr_url": JOBLESS_PR, "pr": {"state": "open"}, "effect": "repeat", "why": "hidden-event-jobless-pr"},
        f"merge-sync:{JOBLESS_PR}:pr_open:{NOW.isoformat()}",
    )
    await _event(
        db,
        CROSSING,
        "merged",
        {"updates": {"merge_sha": MERGE_SHA}, "pr_url": HIDDEN_PR, **applied, "why": "hidden-event-merge"},
        to="merged",
    )
    await _event(
        db,
        CROSSING,
        "pr_updated",
        {"pr_url": OWN_PR, "pr": {}, "effect": "repeat", "why": "hidden-event-key"},
        f"existing-pr:{JOB_ELSEWHERE}",
    )
    await _event(
        db,
        CROSSING,
        "pr_closed",
        {"updates": {}, "effect": "noted", "why": "hidden-event-pr-key"},
        f"merge-sync:{HIDDEN_PR}:closed:{NOW.isoformat()}",
    )
    await _event(
        db,
        CROSSING,
        "abandoned",
        {
            "updates": {"resolution_path": "abandoned", "note": "gave up"},
            "effect": "noted",
            "why": "hidden-event-abandoned",
        },
        f"job-abandoned:{JOB_ELSEWHERE}",
    )
    await _event(
        db,
        CROSSING,
        "pr_updated",
        {"pr_url": OWN_PR, "pr": {}, "effect": "repeat", "why": "hidden-event-job-pr-key"},
        f"job-pr:{JOB_ELSEWHERE}",
    )
    await _event(
        db,
        CROSSING,
        "verdict_recorded",
        {"updates": {"caused_regression_case_id": NO_REPOSITORY}, "effect": "noted", "why": "hidden-event-regression"},
    )
    for url, why in ((GITLAB_MR, "hidden-event-gitlab"), (NO_REPOSITORY_PR, "hidden-event-no-repository-pr")):
        await _event(
            db, CROSSING, "pr_opened", {"pr_url": url, "pr": {"state": "open"}, "effect": "repeat", "why": why}
        )
    await _event(db, CROSSING, "noted", {"updates": {"note": covered}, "effect": "noted", "why": "shown-event-note"})


#: The job an accepted case's dispatch answers with: the executor is a stand-in that starts nothing.
STARTED = "job-started-here"


@asynccontextmanager
async def case_reads_app(
    db: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[FastAPI, Keys, Mapping[str, Mapping[str, str]]]]:
    """:func:`tests.fixtures.issue_reads.issue_reads_app`, each case given what the case routes read. Accepting a case
    reaches the executor, whose starts are recorded and run by no one."""
    async with issue_reads_app(db, monkeypatch) as (app, keys, values):
        await seed_cases(db)
        executor = app.state.issue_executor
        monkeypatch.setattr(executor, "execute", AsyncMock(return_value=STARTED))
        monkeypatch.setattr(executor, "schedule_resume", MagicMock())
        monkeypatch.setattr(executor, "cancel_job", AsyncMock())
        yield app, keys, values


#: Every case id no key with an allowlist reads.
HIDDEN_CASES = (CASE[UNCOVERED], *OWNER_CASE.values(), NO_REPOSITORY)


def shows_hidden_case(text: str) -> bool:
    """Whether an answer shows a case no key with an allowlist covering only :data:`COVERED` reads: its id, its pull
    request, or anything stored for it."""
    return any(needle in text for needle in (*HIDDEN_CASES, *HIDDEN_MARKS, PR[UNCOVERED]))


def shows_crossing(text: str) -> bool:
    """Whether an answer shows what :data:`CROSSING` links to in the other repository: a hidden case, its issue, its
    job, a pull request there or of that job, or its history naming any of it."""
    return shows_hidden_case(text) or any(
        needle in text for needle in (MIXED_HIDDEN, JOB_ELSEWHERE, HIDDEN_PR, JOBLESS_PR, MERGE_SHA, *HIDDEN_EVENTS)
    )
