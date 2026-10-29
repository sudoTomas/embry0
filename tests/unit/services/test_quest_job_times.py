"""#216: a finished job's diary tasks and quest steps keep the time it finished, whatever its pull request does.

Merge sync records what became of a job's pull request (opened, merged,
closed, reopened) long after the job finished. A task or step about the job's
own work stays at the time the job finished; one about a merge reads the time
the pull request merged. Each test seeds the jobs, records the pull request
through merge sync's own recorder, and reads the diaries and quests from the
journal, as a read of ``GET /progression`` does.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.services.merge_sync.facts import DeliveryState, PullRequestFact
from embry0.services.merge_sync.recorder import DeliveryRecorder
from embry0.services.progression_pack import default_rules
from embry0.services.quests import Diary, evaluate_diaries, evaluate_quests, run_agents
from embry0.storage.repositories.job_delivery import JobDeliveryRepository
from embry0.storage.repositories.progression import ProgressionRepository

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

REPO = "acme/widgets"
T0 = datetime(2026, 3, 2, 9, tzinfo=UTC)
RULES = default_rules()
QUIET_DAYS = 30  # the shipped pack's quiet stretch

#: The diary task kinds about a job's own work, never a merge.
FINISHED_KINDS = ("finish_job", "run_qa", "pass_qa", "audit", "clean_audit", "quiet_stretch")


def hour(n: float) -> datetime:
    return T0 + timedelta(hours=n)


def day(n: float) -> datetime:
    return T0 + timedelta(days=n)


def _url(number: int) -> str:
    return f"https://github.com/{REPO}/pull/{number}"


class _Issues:
    """The recorder closes a merged job's issue; these tests read jobs only."""

    async def update(self, issue_id: str | None, **fields: Any) -> None:
        return None


async def _job(
    db,
    job_id: str,
    *,
    finished: datetime,
    status: str = "completed",
    template: str | None = None,
    issue: str | None = None,
    report: str | None = None,
    pr: int | None = None,
) -> None:
    """A job created half an hour before it finished, with an open pull request numbered ``pr``."""
    await db.execute(
        """
        INSERT INTO jobs (job_id, repo, task, status, issue_id, pipeline_template, result_summary, pr_url,
                          delivery_state, created_at, started_at, finished_at, updated_at, budget_overrun_usd)
        VALUES ($1, $2, 't', $3, $4, $5, $6, $7, $8, $9, $9, $10, $10, 0)
        """,
        job_id,
        REPO,
        status,
        issue,
        template,
        report,
        _url(pr) if pr is not None else None,
        "pr_open" if pr is not None else "none",
        finished - timedelta(minutes=30),
        finished,
    )


async def _run(db, trace_id: str, job_id: str, agent: str, at: datetime) -> None:
    await db.execute(
        "INSERT INTO traces (trace_id, job_id, agent_type, model, result, created_at) VALUES ($1, $2, $3, 'm', 'success', $4)",
        trace_id,
        job_id,
        agent,
        at,
    )


async def _record(db, pr: int, state: DeliveryState, at: datetime) -> None:
    """What merge sync records when the forge reports pull request ``pr`` in ``state`` at ``at``."""
    fact = PullRequestFact(repo=REPO, number=pr, url=_url(pr), state=state, at=at)
    result = await DeliveryRecorder(JobDeliveryRepository(db), _Issues()).record(fact, source="webhook")
    assert result.changes, f"merge sync recorded nothing for pull request {pr} {state.value} at {at}"


async def _statuses(db) -> dict[str, str]:
    return {r["job_id"]: r["status"] for r in await db.fetch("SELECT job_id, status FROM jobs")}


def _tasks(diary: Diary) -> dict[str, list[datetime | None]]:
    """When each of the diary's tasks was done, by kind, in tier order."""
    done: dict[str, list[datetime | None]] = {}
    for entry in RULES.pack.diaries.tiers:
        for task_entry, task in zip(entry.tasks, diary.tier(entry.id).tasks, strict=True):
            done.setdefault(task_entry.kind, []).append(task.done_at)
    return done


async def _read(db) -> tuple[dict[str, list[datetime | None]], dict[str, list[datetime | None]]]:
    """The repository's diary tasks by kind, and each quest's steps, as a read of the progression has them."""
    journal = await ProgressionRepository(db).journal(run_agents(RULES))
    diaries = evaluate_diaries(journal, rules=RULES)
    assert [d.repo for d in diaries] == [REPO], [d.repo for d in diaries]
    quests = evaluate_quests(journal, diaries, lambda _total: None, rules=RULES)
    return _tasks(diaries[0]), {q.id: [s.done_at for s in q.steps] for q in quests}


async def test_the_quiet_stretch_stays_done_when_a_pull_request_merges_after_a_failure(db_with_migrations) -> None:
    """The issue's case: a job finishes 31 days after the first, one fails a day later, and the first one merges."""
    db = db_with_migrations
    await _job(db, "job-first", finished=day(0))
    await _job(db, "job-quiet", finished=day(QUIET_DAYS + 1), pr=1)
    await _job(db, "job-failed", status="failed", finished=day(QUIET_DAYS + 2))
    before, _ = await _read(db)
    assert before["quiet_stretch"] == [day(QUIET_DAYS + 1)], f"the quiet stretch is not done on day 31: {before}"

    await _record(db, 1, DeliveryState.MERGED, day(QUIET_DAYS + 9))
    assert (await _statuses(db))["job-quiet"] == "pr_merged", "merge sync did not move the job to pr_merged"
    after, _ = await _read(db)
    assert after["quiet_stretch"] == before["quiet_stretch"], (
        f"the pull request of the job that finished on day {QUIET_DAYS + 1} merged on day {QUIET_DAYS + 9}, "
        f"after the failure on day {QUIET_DAYS + 2}: the quiet stretch done at {before['quiet_stretch']} "
        f"now reads {after['quiet_stretch']}"
    )


async def test_a_finished_jobs_tasks_and_steps_keep_their_time_as_its_pull_request_opens_closes_reopens_and_merges(
    db_with_migrations,
) -> None:
    """Every task and step about a job's own work reads when the job finished, through its pull request's life."""
    db = db_with_migrations
    await db.execute("INSERT INTO issues (id, title, repo) VALUES ('iss-1', 'Fix the thing', $1)", REPO)
    await _job(db, "job-onboard", template="onboard", finished=hour(1), pr=1)
    await _job(db, "job-plain", finished=hour(2), pr=2)
    await _job(db, "job-qa", template="qa", finished=hour(3), pr=3)
    await _job(db, "job-audit", template="context-audit", report="Stale: 0\nFiles: 4\n", finished=hour(4), pr=4)
    await _job(db, "job-failed", status="failed", issue="iss-1", finished=hour(5), pr=5)
    await _job(db, "job-quiet", finished=day(QUIET_DAYS + 1), pr=6)

    async def finished_times() -> dict[str, Any]:
        tasks, quests = await _read(db)
        return {
            **{kind: tasks[kind] for kind in FINISHED_KINDS},
            "first_steps (onboarded, finished)": quests["first_steps"][:2],
            "try_again (failed)": quests["try_again"][:1],
        }

    want = await finished_times()
    assert want == {
        "finish_job": [hour(1)],
        "run_qa": [hour(3)],
        "pass_qa": [hour(3)],
        "audit": [hour(4)],
        "clean_audit": [hour(4)],
        "quiet_stretch": [day(QUIET_DAYS + 1)],
        "first_steps (onboarded, finished)": [hour(1), hour(2)],
        "try_again (failed)": [hour(5)],
    }, f"before any pull request moved, the tasks and steps are not at the times the jobs finished: {want}"

    lives = [
        ("opened", DeliveryState.PR_OPEN, day(40), "completed"),
        ("closed", DeliveryState.CLOSED, day(41), "pr_closed"),
        ("reopened", DeliveryState.PR_OPEN, day(42), "completed"),
        ("merged", DeliveryState.MERGED, day(43), "pr_merged"),
    ]
    for phase, state, at, status in lives:
        for pr in range(1, 7):
            await _record(db, pr, state, at)
        statuses = await _statuses(db)
        assert statuses.pop("job-failed") == "failed" and set(statuses.values()) == {status}, (
            f"the pull requests {phase}: merge sync left the statuses at {statuses}"
        )
        got = await finished_times()
        assert got == want, f"the pull requests {phase} on {at:%d %b}: the tasks and steps went from {want} to {got}"


async def test_a_task_or_step_about_a_merge_reads_when_the_pull_request_merged(db_with_migrations) -> None:
    """A failed attempt, then a fix that merges three days after it finished; the attempt's pull request closes later.

    The merge tasks and steps read the merge, never when the fix finished;
    the failure reads when the attempt failed, never when its pull request
    was closed.
    """
    db = db_with_migrations
    await db.execute("INSERT INTO issues (id, title, repo) VALUES ('iss-1', 'Fix the thing', $1)", REPO)
    await _job(db, "job-attempt", status="failed", issue="iss-1", finished=hour(1), pr=1)
    await _job(db, "job-fix", issue="iss-1", finished=hour(3), pr=2)
    await _run(db, "trc-review", "job-fix", "review", hour(2))
    await _run(db, "trc-qa", "job-fix", "qa", hour(2.5))
    merged = day(3)

    await _record(db, 2, DeliveryState.MERGED, merged)
    await _record(db, 1, DeliveryState.CLOSED, day(4))
    assert await _statuses(db) == {"job-attempt": "failed", "job-fix": "pr_merged"}
    tasks, quests = await _read(db)
    assert tasks["merges"][0] == merged, f"the first merge task is not the merge on {merged}: {tasks['merges']}"
    assert tasks["pass_qa"] == [merged], f"a merge on an issue QA ran on passes QA when it merged: {tasks['pass_qa']}"
    assert quests["gatekeeper"] == [hour(2), hour(2.5), merged], (
        f"the gatekeeper's merge is not the merge on {merged}: {quests['gatekeeper']}"
    )
    assert quests["try_again"] == [hour(1), merged], (
        f"the attempt failed at {hour(1)} and the fix merged at {merged}, the attempt's pull request closed after "
        f"that: the try-again steps read {quests['try_again']}"
    )
