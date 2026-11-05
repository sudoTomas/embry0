"""Every write route that acts on one issue answers a key acting on another repository's issue as on a missing one, and
what an action reaches of another repository is refused without naming it.

On the real app over a real database (:mod:`tests.fixtures.issue_reads`).

- Each write route whose path names an issue is found by the walk of
  ``test_repository_writes_inventory.py`` and called through an admin key
  whose list covers one repository: on the other repository's issue, then
  on an issue that does not exist, with the same body. The two answers must
  be the same, status, headers and body, each id put back as ``<issue>``, so
  an answer never tells whether the issue exists. The first is refused all
  the same: logged and audited once, naming the repository; the second is
  not. Through a key without a list the route must reach the issue. That the
  refused call writes nothing is that walk's own check.
- Answering a question that a job of another repository asked about the
  key's issue, or that the key's job asked about another repository's issue,
  answers as answering a question that does not exist, audited, and answers
  nothing.
- Deleting the key's issue when that would cancel a child issue or an active
  job of another repository is refused, writes nothing and names only the
  key's own repository; the log line and the audit row name the other.
- The key's change of an issue's status does not roll up into a parent of
  another repository, and what it answers names no such parent.
- No write on the key's job links new work to an issue of another
  repository, and none names it: running a job again starts the new job
  with no issue, from the job's own task.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.api.repo_allowlist import REFUSAL_CODE
from embry0.storage.database import DatabasePool
from tests.fixtures.issue_reads import (
    CHILD,
    CHILD_ELSEWHERE,
    CHILD_HERE,
    INPUT,
    INPUT_ELSEWHERE,
    INPUT_HERE,
    ISSUE,
    JOB_ELSEWHERE,
    JOB_HERE,
    MARK,
    MISSING,
    MISSING_INPUT,
    MIXED,
    MIXED_HIDDEN,
    issue_reads_app,
    same_answer,
    shows_uncovered_issue,
)
from tests.fixtures.job_reads import JOB
from tests.fixtures.repository_writes import COVERED, NOW, OWNER, UNCOVERED, call
from tests.unit.api.test_repository_writes_inventory import BODIES, READS, _app, _refusals, _tables, write_routes

pytestmark = pytest.mark.requires_postgres

_PARAMETER = re.compile(r"\{(\w+)(?::\w+)?\}")

#: Every write route whose path names an issue, as (method, path).
ROUTES = [(method, path) for method, path in write_routes(_app()) if "{issue_id}" in path and path not in READS]


def test_the_walk_finds_the_routes_that_act_on_an_issue() -> None:
    assert set(ROUTES) >= {
        ("PUT", "/api/v1/issues/{issue_id}"),
        ("DELETE", "/api/v1/issues/{issue_id}"),
        ("POST", "/api/v1/issues/{issue_id}/triage"),
        ("POST", "/api/v1/issues/{issue_id}/sync"),
        ("POST", "/api/v1/issues/{issue_id}/inputs/{input_id}/answer"),
    }, ROUTES


def _quiet(app: Any) -> MagicMock:
    """The executor's starts and stops, recorded and run by no one: a call through a key without a list must reach
    the issue, and nothing it starts runs here."""
    executor: MagicMock = app.state.issue_executor
    executor.cancel_job = AsyncMock()
    executor.schedule_resume = MagicMock()
    executor.execute = AsyncMock(return_value="job-started-here")
    return executor


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
async def test_acting_on_another_repository_s_issue_answers_as_on_one_that_does_not_exist(
    method: str, path: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, keys, values):
        _quiet(app)
        named = values[UNCOVERED]
        body = BODIES.get(path)

        def url(issue: str) -> str:
            return _PARAMETER.sub(lambda m: issue if m.group(1) == "issue_id" else named[m.group(1)], path)

        before = await _refusals(db)
        uncovered = await call(app, method, url(ISSUE[UNCOVERED]), keys.limited, body)
        refused = (await _refusals(db))[len(before) :]
        missing = await call(app, method, url(MISSING), keys.limited, body)
        after_missing = (await _refusals(db))[len(before) + len(refused) :]
        reached = await call(app, method, url(ISSUE[UNCOVERED]), keys.open, body)
    differ = same_answer(uncovered, {ISSUE[UNCOVERED]: "<issue>"}, missing, {MISSING: "<issue>"})
    assert differ is None, f"{method} {path}: another repository's issue answers as a missing one: {differ}"
    assert uncovered.status_code == 404, f"{method} {path}: {uncovered.status_code} {uncovered.text[:200]}"
    assert refused == [UNCOVERED], f"{method} {path}: the refusal is audited once, naming {UNCOVERED}: {refused}"
    assert after_missing == [], f"{method} {path}: a missing issue's answer audits nothing: {after_missing}"
    assert reached.status_code != 404 and reached.status_code < 500, (
        f"{method} {path}: a key without an allowlist reaches the issue: {reached.status_code} {reached.text[:200]}"
    )


#: Each way to answer a question asked about the work of both repositories: the URL of the question, of a question
#: that does not exist on the same issue or job, and the question's id.
CROSSED_ANSWERS: dict[str, tuple[str, str, str, Any, Any, str]] = {
    "an issue's question asked by a job elsewhere": (
        f"/api/v1/issues/{MIXED}/inputs/{INPUT_ELSEWHERE}/answer",
        f"/api/v1/issues/{MIXED}/inputs/{MISSING_INPUT}/answer",
        INPUT_ELSEWHERE,
        {"answer": "this one"},
        {"answer": "this one"},
        "Input not found",
    ),
    "a job's question about an issue elsewhere": (
        f"/api/v1/jobs/{JOB_HERE}/inputs/{INPUT_HERE}/answer",
        f"/api/v1/jobs/{JOB_HERE}/inputs/{MISSING_INPUT}/answer",
        INPUT_HERE,
        {"answer": "this one"},
        {"answer": "this one"},
        "Input not found",
    ),
    "a job's question about an issue elsewhere, answered in a chat": (
        f"/api/v1/asks/jobs/{JOB_HERE}/answer",
        f"/api/v1/asks/jobs/{JOB_HERE}/answer",
        INPUT_HERE,
        {"person": "ada@example.com", "input_id": INPUT_HERE, "answer": "this one"},
        {"person": "ada@example.com", "input_id": MISSING_INPUT, "answer": "this one"},
        "that job has no such question",
    ),
}


@pytest.mark.parametrize("case", list(CROSSED_ANSWERS))
async def test_answering_a_question_about_another_repository_s_work_answers_as_a_missing_question(
    case: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    url, missing_url, question, body, missing_body, detail = CROSSED_ANSWERS[case]
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        executor = _quiet(app)
        await _starter(db)
        before = await _refusals(db)
        crossed = await call(app, "POST", url, keys.limited, body)
        refused = (await _refusals(db))[len(before) :]
        missing = await call(app, "POST", missing_url, keys.limited, missing_body)
        after_missing = (await _refusals(db))[len(before) + len(refused) :]
        unanswered = await db.fetchval("SELECT status FROM issue_inputs WHERE id = $1", question)
        resumed = executor.schedule_resume.call_count
        reached = await call(app, "POST", url, keys.open, body)
        own = await call(
            app,
            "POST",
            f"/api/v1/issues/{ISSUE[COVERED]}/inputs/{INPUT[COVERED]}/answer",
            keys.limited,
            {"answer": "this one"},
        )
    differ = same_answer(crossed, {question: "<input>"}, missing, {MISSING_INPUT: "<input>"})
    assert differ is None, f"{case}: answers as a question that does not exist: {differ}"
    assert crossed.status_code == 404 and crossed.json() == {"detail": detail}, crossed.text
    assert refused == [UNCOVERED], f"{case}: the refusal is audited once, naming {UNCOVERED}: {refused}"
    assert after_missing == [], f"{case}: a missing question's answer audits nothing: {after_missing}"
    assert unanswered == "pending" and resumed == 0, f"{case}: the refused answer recorded or resumed nothing"
    assert reached.status_code == 200 and reached.json()["status"] == "answered", (
        f"{case}: a key without an allowlist answers it: {reached.status_code} {reached.text[:200]}"
    )
    assert own.status_code == 200, f"the limited key answers its own issue's question: {own.text[:200]}"


#: An issue of the covered repository with a child elsewhere and nothing else of another repository, and one with an
#: active job elsewhere and nothing else.
ONLY_CHILD, ONLY_JOB = "iss-only-child", "iss-only-job"


async def _reaching(db: DatabasePool) -> None:
    for issue in (ONLY_CHILD, ONLY_JOB):
        await db.execute("INSERT INTO issues (id, title, repo, status) VALUES ($1, 'Mine', $2, 'open')", issue, COVERED)
    await db.execute(
        "INSERT INTO issues (id, title, repo, status, parent_issue_id) VALUES ('iss-kid-only', 'Theirs', $1, 'open', $2)",
        UNCOVERED,
        ONLY_CHILD,
    )
    await db.execute(
        "INSERT INTO jobs (job_id, repo, task, status, issue_id, created_at) VALUES ('job-only-gizmo', $1, 't', "
        "'running', $2, $3)",
        UNCOVERED,
        ONLY_JOB,
        NOW,
    )


@pytest.mark.parametrize(
    ("issue", "reached_there"),
    [(MIXED, f"{CHILD_ELSEWHERE} and {JOB_ELSEWHERE}"), (ONLY_CHILD, "a child"), (ONLY_JOB, "a running job")],
    ids=["child-and-job", "child", "job"],
)
async def test_deleting_an_issue_that_would_cancel_another_repository_s_work_is_refused_without_naming_it(
    issue: str, reached_there: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        await _reaching(db)
        executor = _quiet(app)
        tables, before = await _tables(db), await _refusals(db)
        refused = await call(app, "DELETE", f"/api/v1/issues/{issue}", keys.limited)
        audited = (await _refusals(db))[len(before) :]
        changed = sorted(name for name, digest in (await _tables(db)).items() if tables.get(name) != digest)
        cancelled = executor.cancel_job.await_count
        reached = await call(app, "DELETE", f"/api/v1/issues/{issue}", keys.open)
    detail = refused.json().get("detail", "") if refused.status_code == 403 else refused.text
    assert refused.status_code == 403 and detail.startswith(REFUSAL_CODE), (
        f"deleting {issue}, which would cancel {reached_there} of {UNCOVERED}, is refused: {refused.status_code} "
        f"{refused.text[:200]}"
    )
    assert COVERED in detail and UNCOVERED not in detail and OWNER[UNCOVERED] not in detail, (
        f"the refusal names the key's own repository and not the other: {detail}"
    )
    assert audited == [UNCOVERED], f"the refusal is audited once, naming {UNCOVERED}: {audited}"
    assert changed == [] and cancelled == 0, f"the refused delete wrote to {changed} and cancelled {cancelled} jobs"
    assert reached.status_code == 204, f"a key without a list deletes it: {reached.status_code} {reached.text[:200]}"


async def test_an_issue_the_key_changes_or_triages_counts_only_its_repositories_work(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIXED is the key's, with a child and a job elsewhere: what changing it and triaging it answer counts neither."""
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        _quiet(app)
        counts = {}
        for name, token in (("limited", keys.limited), ("open", keys.open)):
            changed = (await call(app, "PUT", f"/api/v1/issues/{MIXED}", token, {"title": f"by {name}"})).json()
            triaged = await call(app, "POST", f"/api/v1/issues/{MIXED}/triage", token)
            await call(app, "PUT", f"/api/v1/issues/{MIXED}", token, {"status": "open"})
            counts[name] = [(body["children_count"], body["jobs_count"]) for body in (changed, triaged.json())]
    assert counts == {"limited": [(0, 0), (0, 0)], "open": [(1, 1), (1, 1)]}, (
        f"the issue a key changes or triages counts only the children and jobs of its repositories: {counts}"
    )


async def test_the_key_deletes_its_own_issue_and_its_own_children(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        _quiet(app)
        deleted = await call(app, "DELETE", f"/api/v1/issues/{ISSUE[COVERED]}", keys.limited)
        statuses = {
            r["id"]: r["status"] for r in await db.fetch("SELECT id, status FROM issues WHERE repo = $1", COVERED)
        }
    assert deleted.status_code == 204, deleted.text
    assert statuses[ISSUE[COVERED]] == "cancelled" and statuses["iss-kid-widget"] == "cancelled", statuses


async def _statuses(db: DatabasePool, *issue_ids: str) -> tuple[str, ...]:
    return tuple([str(await db.fetchval("SELECT status FROM issues WHERE id = $1", i)) for i in issue_ids])


async def test_a_key_s_status_change_does_not_roll_up_into_a_parent_it_does_not_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CHILD_HERE is the key's, under a parent of the other repository: moving it through in progress to closed
    leaves that parent as it was, refuses nothing, and names no parent; a key without a list moves the parent with
    it, and the key's own child still moves its own parent."""
    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        _quiet(app)
        seen: dict[str, Any] = {}
        for name, token in (("limited", keys.limited), ("open", keys.open)):
            steps = []
            for status in ("in_progress", "closed", "open"):
                changed = await call(app, "PUT", f"/api/v1/issues/{CHILD_HERE}", token, {"status": status})
                steps.append(
                    (changed.status_code, changed.json().get("parent_issue_id"), *await _statuses(db, MIXED_HIDDEN))
                )
            seen[name] = steps
        own = await call(app, "PUT", f"/api/v1/issues/{CHILD[COVERED]}", keys.limited, {"status": "closed"})
        own_parent = await _statuses(db, ISSUE[COVERED])
    assert seen["limited"] == [(200, None, "open")] * 3, (
        f"the key's change of {CHILD_HERE} rolls up into no parent of {UNCOVERED}, names none, refuses nothing: "
        f"{seen['limited']}"
    )
    assert seen["open"] == [
        (200, MIXED_HIDDEN, "in_progress"),
        (200, MIXED_HIDDEN, "closed"),
        (200, MIXED_HIDDEN, "open"),
    ], f"a key without a list moves the parent with its child: {seen['open']}"
    assert own.status_code == 200 and own_parent == ("closed",), f"the key's own parent still follows: {own_parent}"


async def test_what_the_key_changes_or_triages_names_no_parent_it_does_not_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with issue_reads_app(db_with_migrations, monkeypatch) as (app, keys, _values):
        _quiet(app)
        named = {}
        for name, token in (("limited", keys.limited), ("open", keys.open)):
            changed = await call(app, "PUT", f"/api/v1/issues/{CHILD_HERE}", token, {"title": f"by {name}"})
            triaged = await call(app, "POST", f"/api/v1/issues/{CHILD_HERE}/triage", token)
            await call(app, "PUT", f"/api/v1/issues/{CHILD_HERE}", token, {"status": "open"})
            named[name] = (changed.json()["parent_issue_id"], triaged.json()["parent_issue_id"])
    assert named == {"limited": (None, None), "open": (MIXED_HIDDEN, MIXED_HIDDEN)}, (
        f"what changing and triaging {CHILD_HERE} answers names no parent of {UNCOVERED}: {named}"
    )


#: The key's finished job and its paused one, each linked to the other repository's issue, as when the issue moved
#: after the job ran; an issue's workflow job, so running it again starts from its issue.
CROSSED_DONE, CROSSED_PAUSED = "job-done-crossed", "job-paused-crossed"
#: The task the issue's title and body made, as IssueExecutor.execute writes it.
CROSSED_TASK = "Fix the header\n\nThe header overlaps the menu."


async def _starter(db: DatabasePool) -> None:
    """The person who started the crossing jobs, known to embry0, as a chat names them."""
    await db.execute(
        "INSERT INTO users (id, auth0_sub, email, name, tier, github_login) VALUES ('usr-ada', 'auth0|ada', "
        "'ada@example.com', 'ada', 'operator', 'ada') ON CONFLICT DO NOTHING"
    )


async def _crossed_jobs(db: DatabasePool) -> None:
    await _starter(db)
    for job, status in ((CROSSED_DONE, "completed"), (CROSSED_PAUSED, "paused")):
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, issue_id, issue_number, launch, pr_url, "
            "created_at, finished_at, dispatched_by) "
            "VALUES ($1, $2, $3, $4, 'full-magnum-opus', $5, 12, $6, $7, $8, $8, 'ada@example.com')",
            job,
            COVERED,
            CROSSED_TASK,
            status,
            MIXED_HIDDEN,
            {"runner": "workflow", "issue_id": MIXED_HIDDEN, "additional_context": ""},
            f"https://github.com/{COVERED}/pull/9",
            NOW,
        )


async def _links(db: DatabasePool) -> int:
    return int(await db.fetchval("SELECT count(*) FROM jobs WHERE issue_id = $1", MIXED_HIDDEN))


#: Every write route whose path names a job, as (method, path); the token routes store nothing and are reads.
JOB_WRITES = [(method, path) for method, path in write_routes(_app()) if "{job_id}" in path and path not in READS]
#: Job writes held to the rule by a test of their own, and which.
JOB_WRITES_ELSEWHERE = {
    **{
        path: (
            "answering a question about another repository's issue answers as a missing question: "
            "test_answering_a_question_about_another_repository_s_work_answers_as_a_missing_question"
        )
        for path in ("/api/v1/jobs/{job_id}/inputs/{input_id}/answer", "/api/v1/asks/jobs/{job_id}/answer")
    },
}
#: The body each job write is sent with here, where the write inventory's would not reach the key's own job.
JOB_WRITE_BODIES: dict[str, Any] = {
    "/api/v1/asks/jobs/{job_id}/resume": {"person": "ada@example.com", "choice": "abandon"},
    "/api/v1/asks/jobs/{job_id}/stop": {"person": "ada@example.com"},
}


def _crossed_job(path: str) -> str:
    """The job each write acts on: the paused one for resume, the active one for a cancel or a done-when list, the
    finished one for a review."""
    if path.endswith(("/resume", "/stop")):
        return CROSSED_PAUSED
    if path.endswith(("/cancel", "/done-when")):
        return JOB_HERE
    return CROSSED_DONE


def test_the_walk_finds_the_writes_on_a_job() -> None:
    paths = {path for _method, path in JOB_WRITES}
    assert {
        "/api/v1/jobs/{job_id}/cancel",
        "/api/v1/jobs/{job_id}/resume",
        "/api/v1/jobs/{job_id}/run-again",
        "/api/v1/jobs/{job_id}/send-back",
        "/api/v1/world/quests/{job_id}/run-again",
    } <= paths, sorted(paths)
    assert set(JOB_WRITES_ELSEWHERE) <= paths, "listed here but not served"


@pytest.mark.parametrize(
    ("method", "path"),
    [(m, p) for m, p in JOB_WRITES if p not in JOB_WRITES_ELSEWHERE],
    ids=[f"{m} {p}" for m, p in JOB_WRITES if p not in JOB_WRITES_ELSEWHERE],
)
async def test_a_write_on_the_key_s_job_links_no_new_work_to_an_issue_it_does_not_read_and_names_none(
    method: str, path: str, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        await _crossed_jobs(db)
        _quiet(app)
        app.state.issue_executor.run_again = MagicMock()
        job = _crossed_job(path)
        before = await _links(db)
        answer = await call(
            app, method, path.replace("{job_id}", job), keys.limited, JOB_WRITE_BODIES.get(path, BODIES.get(path))
        )
        after = await _links(db)
    assert answer.status_code not in (401, 403, 404) and answer.status_code < 500, (
        f"{method} {path}: the key reaches its own job {job}: {answer.status_code} {answer.text[:200]}"
    )
    assert not shows_uncovered_issue(answer.text), f"{method} {path}: the answer names {MIXED_HIDDEN}: {answer.text}"
    assert after == before, f"{method} {path}: the key's write linked new work to {MIXED_HIDDEN}: {before} -> {after}"


async def test_running_the_key_s_job_again_starts_new_work_with_no_issue_it_does_not_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The new job has no issue and no issue number, its workflow runs from the job's own task with no tracker, and
    the answer says so; a key without a list starts it on the issue, and the key's own issue stays linked."""
    db = db_with_migrations
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        await _crossed_jobs(db)
        await db.execute(
            "UPDATE jobs SET launch = $2 WHERE job_id = $1",
            JOB[COVERED],
            {"runner": "workflow", "issue_id": ISSUE[COVERED]},
        )
        _quiet(app)
        launched = app.state.issue_executor.run_again = MagicMock()
        started = {}
        for name, token, job in (
            ("limited", keys.limited, CROSSED_DONE),
            ("open", keys.open, CROSSED_DONE),
            ("own", keys.limited, JOB[COVERED]),
        ):
            answer = await call(app, "POST", f"/api/v1/jobs/{job}/run-again", token)
            assert answer.status_code == 202, answer.text
            new = answer.json()["followup_job_id"]
            row = await db.fetchrow("SELECT issue_id, issue_number, task FROM jobs WHERE job_id = $1", new)
            assert row is not None, f"{name}: the new job {new} has a row"
            call_ = launched.call_args
            started[name] = {
                "answer": answer.json().get("followup_issue_id", "(not in the answer)"),
                "row": (row["issue_id"], row["issue_number"], row["task"]),
                "launch": call_.args[1],
                "issue_id": call_.kwargs["issue_id"],
            }
    assert started["limited"] == {
        "answer": None,
        "row": (None, None, CROSSED_TASK),
        "launch": {
            "runner": "workflow",
            "issue_id": None,
            "additional_context": "",
            "issue": {
                "repo": COVERED,
                "title": "Fix the header",
                "body": "The header overlaps the menu.",
                "tracker": None,
                "tracker_key": None,
            },
        },
        "issue_id": None,
    }, f"the key runs its job again linked to no issue of {UNCOVERED}: {started['limited']}"
    assert started["open"]["answer"] == MIXED_HIDDEN and started["open"]["row"][:2] == (MIXED_HIDDEN, 12), started
    assert started["open"]["launch"] == {"runner": "workflow", "issue_id": MIXED_HIDDEN, "additional_context": ""}
    assert started["own"]["answer"] == ISSUE[COVERED] and started["own"]["issue_id"] == ISSUE[COVERED], started["own"]


async def test_running_the_key_s_job_again_hands_its_workflow_nothing_of_an_issue_it_does_not_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the executor's own relaunch: the workflow of the key's new job gets no issue, the job's own task, and
    none of the issue's answered questions, which the answer flow gathered from every job on the issue, those of other
    repositories among them; a key without a list starts it on the issue, with them. A job posted with a git context
    alone runs again in its context's repository."""
    import asyncio

    from embry0.services.issue_executor import IssueExecutor

    db = db_with_migrations
    answered = f"Q: which one?\nA: the one {MARK[UNCOVERED]}"
    ran: list[dict[str, Any]] = []
    tasks: list[asyncio.Future[Any]] = []

    async def run_workflow(
        self: IssueExecutor, issue_id: str | None, job_id: str, issue: dict[str, Any], additional_context: str = ""
    ) -> None:
        ran.append({"issue_id": issue_id, "issue": issue.get("id") or issue, "additional_context": additional_context})

    def track(self: IssueExecutor, coro: Any, **_kwargs: Any) -> asyncio.Future[Any]:
        task: asyncio.Future[Any] = asyncio.ensure_future(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(IssueExecutor, "_run_workflow", run_workflow)
    monkeypatch.setattr(IssueExecutor, "_track_task", track)
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        await _crossed_jobs(db)
        launch = {"runner": "workflow", "issue_id": MIXED_HIDDEN, "additional_context": answered}
        await db.execute("UPDATE jobs SET launch = $2::jsonb WHERE job_id = $1", CROSSED_DONE, launch)
        # The same job posted with a git context alone, which stores no repo.
        await db.execute(
            "INSERT INTO jobs (job_id, repo, context, task, status, issue_id, issue_number, launch, created_at, "
            "finished_at, dispatched_by) SELECT 'job-done-crossed-context', NULL, $2::jsonb, task, status, issue_id, "
            "issue_number, launch, created_at, finished_at, dispatched_by FROM jobs WHERE job_id = $1",
            CROSSED_DONE,
            {"type": "git", "repo": COVERED},
        )
        for job, token in (
            (CROSSED_DONE, keys.limited),
            (CROSSED_DONE, keys.open),
            ("job-done-crossed-context", keys.limited),
        ):
            started = await call(app, "POST", f"/api/v1/jobs/{job}/run-again", token)
            assert started.status_code == 202, started.text
            await asyncio.gather(*tasks)
    assert ran[0] == {
        "issue_id": None,
        "issue": {
            "repo": COVERED,
            "title": "Fix the header",
            "body": "The header overlaps the menu.",
            "tracker": None,
            "tracker_key": None,
        },
        "additional_context": "",
    }, f"the key's new job runs from its own task, with nothing of {MIXED_HIDDEN}: {ran[0]}"
    assert ran[1] == {"issue_id": MIXED_HIDDEN, "issue": MIXED_HIDDEN, "additional_context": answered}, ran[1]
    assert ran[2] == ran[0], f"a job posted with a git context alone runs again in its context's repository: {ran[2]}"


#: Jobs posted with a git context alone, which store no ``repo`` (rows from before such a job stored one): two of the
#: key's repository, paused and asking, and one of the other repository, paused.
CONTEXT_PAUSED, CONTEXT_ASKING, CONTEXT_ELSEWHERE = "job-context-paused", "job-context-asking", "job-context-gizmo"


async def _context_only(db: DatabasePool) -> None:
    await _starter(db)
    for job, repo, status in (
        (CONTEXT_PAUSED, COVERED, "paused"),
        (CONTEXT_ASKING, COVERED, "awaiting_input"),
        (CONTEXT_ELSEWHERE, UNCOVERED, "paused"),
    ):
        await db.execute(
            "INSERT INTO jobs (job_id, repo, context, task, status, created_at, dispatched_by) "
            "VALUES ($1, NULL, $2::jsonb, 'Fix it', $3, $4, 'ada@example.com')",
            job,
            {"type": "git", "repo": repo},
            status,
            NOW,
        )
    await db.execute(
        "INSERT INTO issue_inputs (id, issue_id, job_id, question) VALUES ('in-context', NULL, $1, 'which?')",
        CONTEXT_ASKING,
    )


async def test_the_chat_routes_read_a_job_posted_with_a_git_context_alone_as_its_repository_s(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job whose repository is in its git context alone is the key's on the chats' routes as on the dashboard's:
    listed for the person who started it, answered, resumed and stopped; the other repository's is not there. Every
    caller reads such a job's repository from its context, a key without a list too."""
    db = db_with_migrations
    person = "ada@example.com"
    async with issue_reads_app(db, monkeypatch) as (app, keys, _values):
        _quiet(app)
        await _context_only(db)
        waiting = (await call(app, "GET", f"/api/v1/asks/of?person={person}", keys.limited)).json()
        everyone = (await call(app, "GET", f"/api/v1/asks/of?person={person}", keys.open)).json()
        answered = await call(
            app,
            "POST",
            f"/api/v1/asks/jobs/{CONTEXT_ASKING}/answer",
            keys.limited,
            {"person": person, "input_id": "in-context", "answer": "this one"},
        )
        resumed = await call(
            app,
            "POST",
            f"/api/v1/asks/jobs/{CONTEXT_PAUSED}/resume",
            keys.limited,
            {"person": person, "choice": "continue_retrying"},
        )
        stopped = await call(app, "POST", f"/api/v1/asks/jobs/{CONTEXT_PAUSED}/stop", keys.limited, {"person": person})
        elsewhere = await call(
            app,
            "POST",
            f"/api/v1/asks/jobs/{CONTEXT_ELSEWHERE}/resume",
            keys.limited,
            {"person": person, "choice": "continue_retrying"},
        )
        dashboard = await call(app, "GET", f"/api/v1/jobs/{CONTEXT_PAUSED}", keys.limited)
    paused = {(j["job_id"], j["repo"]) for j in waiting["paused"]}
    asking = {(q["job_id"], q["repo"], q["input_id"]) for q in waiting["questions"]}
    assert (CONTEXT_PAUSED, COVERED) in paused and CONTEXT_ELSEWHERE not in str(waiting), (
        f"the key's context-only job is listed with its repository, the other's is not: {waiting}"
    )
    assert (CONTEXT_ASKING, COVERED, "in-context") in asking, f"and its question: {waiting['questions']}"
    assert {(CONTEXT_PAUSED, COVERED), (CONTEXT_ELSEWHERE, UNCOVERED)} <= {
        (j["job_id"], j["repo"]) for j in everyone["paused"]
    }, f"a key without a list reads each one's repository from its context: {everyone['paused']}"
    assert answered.status_code == 200 and answered.json()["status"] == "answered", answered.text[:300]
    assert resumed.status_code == 200 and stopped.status_code == 200, (resumed.text[:200], stopped.text[:200])
    assert elsewhere.status_code == 404 and elsewhere.json() == {"detail": "no job has that id"}, elsewhere.text
    assert dashboard.status_code == 200, "the dashboard's route reads it as the key's too"
