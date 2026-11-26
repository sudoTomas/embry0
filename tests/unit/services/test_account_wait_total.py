"""All of a job's waits for spent accounts together are bounded: ``account_wait.max_total_wait_seconds`` (#173).

A wait is cut at ``max_wait_seconds``, and a job still spent then waits again, so before the cap a job could wait
for an account without end. The job's row now sums what it has spent parked (``jobs.account_waited_seconds``,
migration 160), over every wait, resume and relaunch: the account-wait loop adds each wait's time when it takes the
job back. A new wait is cut to what is left of the total, and once it is spent the job fails with the account's own
error code instead of waiting again. The names this change adds are imported inside each test, so a tree without
them fails each one with its own message.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langgraph.types import Command

from embry0.agents.fallback import Pause
from embry0.safety.error_codes import ErrorCode, classify_failure
from embry0.services import _executor_run
from embry0.services.account_waits import AccountWait, AccountWaitInterrupt, AccountWaitRequired, JobParked
from tests.unit.orchestration.nodes.test_agent_node_spent_account import RESET, _run, _spent
from tests.unit.orchestration.nodes.test_agent_node_spent_account import _Resolver as _NodeResolver
from tests.unit.services.test_spent_account_park import (
    SPENT,
    UNTIL,
    _claude_definitions,  # noqa: F401 - autouse: the job's agents run on Claude, as in that module
    _executor,
    _precheck,
    _Resolver,
    _streaming_executor,
)

DAY = 24 * 3600


def _policy(waited: int = 0, **values: Any) -> Any:
    """What a run gets: the job's account_wait values (``values`` over the defaults) and what it has waited."""
    try:
        from embry0.services.account_waits import WaitPolicy
        from embry0.settings.sections.account_wait import ACCOUNT_WAIT
    except ImportError:
        pytest.fail("no account_wait.max_total_wait_seconds: nothing bounds a job's waits together")
    if "max_total_wait_seconds" not in ACCOUNT_WAIT.model.model_fields:
        pytest.fail("the account_wait section has no max_total_wait_seconds: nothing bounds a job's waits together")
    return WaitPolicy(ACCOUNT_WAIT.validate(values), waited)


# --- the policy --------------------------------------------------------------------


def test_the_default_total_is_thirty_days_and_a_new_job_has_all_of_it() -> None:
    policy = _policy()
    assert policy.settings.max_total_wait_seconds == 30 * DAY, "the default bounds a job's waits at thirty days"
    assert (policy.left_seconds, policy.may_wait) == (30 * DAY, True)


def test_a_wait_is_cut_to_what_is_left_of_the_total() -> None:
    from embry0.services.account_waits import local_busy_wait, wait_for

    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    config = SimpleNamespace(credential_rate_limit_quarantine_seconds=900, credential_quota_quarantine_seconds=3600)
    policy = _policy(waited=30 * DAY - 3600)
    wait = wait_for(Pause("anthropic", "quota_exhausted", now + timedelta(days=5)), config, now, policy=policy)
    assert wait is not None and wait.until == now + timedelta(hours=1), (
        f"a reset five days out is cut to the hour left of the job's total: {wait and wait.until - now}"
    )
    local = local_busy_wait("custom/qwen", "triage", now, policy=_policy(waited=30 * DAY - 60))
    assert local.until == now + timedelta(seconds=60), "a forced local run's retry is cut the same way"


def test_with_nothing_left_the_job_may_not_wait_and_the_failure_says_why() -> None:
    from embry0.services.account_waits import wait_for

    now = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
    spent = _policy(waited=30 * DAY + 5)
    assert (spent.left_seconds, spent.may_wait) == (0, False)
    wait = wait_for(Pause("anthropic", "quota_exhausted", now + timedelta(hours=2)), None, now, policy=spent)
    assert wait is not None, "a spent account is still a wait the job may not take: it says the failure's code"
    assert "max_total_wait_seconds" in spent.why_not() and "720" in spent.why_not(), spent.why_not()


# --- the pre-check -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_job_that_has_waited_its_total_fails_before_it_starts_with_the_account_s_code() -> None:
    _policy()
    resolver = _Resolver(SPENT)
    try:
        await _precheck(_executor(resolver), waited=86400, max_total_wait_seconds=86400)
    except AccountWaitRequired:
        pytest.fail("a job that has waited its account_wait.max_total_wait_seconds was held to wait again")
    except Exception as exc:  # noqa: BLE001 - the failure is what the test reads
        assert classify_failure(exc=exc) == ErrorCode.PROVIDER_QUOTA_EXHAUSTED, repr(exc)
        assert "max_total_wait_seconds" in str(exc), f"the failure names the total it reached: {exc}"
    else:
        pytest.fail("a job that has waited its total, with nowhere to move, started")
    assert resolver.resolved == [], "it fails before any account is resolved or any sandbox exists"


@pytest.mark.asyncio
async def test_a_job_with_an_hour_left_is_held_for_that_hour_only() -> None:
    _policy()
    before = datetime.now(UTC)
    with pytest.raises(AccountWaitRequired) as held:
        await _precheck(_executor(_Resolver(SPENT)), waited=86400 - 3600, max_total_wait_seconds=86400)
    until = held.value.wait.until
    assert before + timedelta(hours=1) <= until <= datetime.now(UTC) + timedelta(hours=1) < UNTIL, (
        f"a reset 15 hours out is cut to the hour left of the job's total: {until - before}"
    )


# --- a run ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_of_a_job_that_has_waited_its_total_fails_with_the_account_s_code() -> None:
    spent = _policy(waited=86400, max_total_wait_seconds=86400)
    try:
        updates, ran, _traces, health, _seen = await _run(order=[], results=[_spent("triage")], account_wait=spent)
    except AccountWaitInterrupt:
        pytest.fail("a run of a job that has waited its total parked it again")
    entry = updates["agent_outputs"][-1]
    assert ran == ["claude-sonnet-5"] and entry["error_code"] == "ERR_PROVIDER_QUOTA_EXHAUSTED", entry
    assert "account_wait" not in entry and health.reports, "no wait is left, and the account is still quarantined"


@pytest.mark.asyncio
async def test_a_run_not_started_on_a_spent_account_says_the_total_was_reached() -> None:
    spent = _policy(waited=86400, max_total_wait_seconds=86400)
    quarantined = _NodeResolver(paused={"anthropic": Pause("anthropic", "quota_exhausted", RESET)})
    updates, ran, *_ = await _run(order=[], resolver=quarantined, waits=False, account_wait=spent)
    entry = updates["agent_outputs"][-1]
    assert ran == [] and entry["error_code"] == "ERR_PROVIDER_QUOTA_EXHAUSTED" and "account_wait" not in entry
    assert "max_total_wait_seconds" in str(entry.get("error_message")), entry.get("error_message")


@pytest.mark.asyncio
async def test_a_run_s_wait_is_cut_to_what_is_left() -> None:
    before = datetime.now(UTC)
    with pytest.raises(AccountWaitInterrupt) as stopped:
        await _run(
            order=[], results=[_spent("triage")], account_wait=_policy(waited=86400 - 600, max_total_wait_seconds=86400)
        )
    until = stopped.value.wait.until
    assert before + timedelta(seconds=600) <= until <= datetime.now(UTC) + timedelta(seconds=600), (
        f"a reset 15 hours out is cut to the ten minutes left: {until - before}"
    )


# --- the job's row: summed over waits, read by each run ---------------------------------


@pytest.mark.asyncio
async def test_each_park_says_since_when_so_the_loop_can_add_the_time(monkeypatch: pytest.MonkeyPatch) -> None:
    _policy()
    executor = _streaming_executor(monkeypatch, _Resolver(SPENT), AsyncMock())
    before = datetime.now(UTC)
    with pytest.raises(JobParked):
        await executor._execute_workflow_stream({"repo": "acme/app", "job_id": "job-1"}, "iss-1", "job-1")
    [call] = [c for c in executor._jobs.update.await_args_list if c.kwargs.get("status") == "paused"]
    since = AccountWait.from_record(call.kwargs["account_wait"]).since  # type: ignore[union-attr]
    assert since is not None and before <= since <= datetime.now(UTC), f"the park records when it began: {since}"


@pytest.mark.asyncio
async def test_a_park_again_after_a_failed_reenrolment_starts_its_own_clock() -> None:
    from embry0.execution.sandbox_manager import SandboxReenrollError
    from tests.unit.services.test_spent_account_park import _resuming_executor

    _policy()
    stream = AsyncMock(side_effect=SandboxReenrollError("proxy restarting", error_code=ErrorCode.SANDBOX_INIT))
    executor = _resuming_executor(stream)
    long_ago = datetime.now(UTC) - timedelta(days=3)
    await executor.resume(
        "iss-1", "job-1", None, account_wait=AccountWait("anthropic", "quota_exhausted", UNTIL, since=long_ago)
    )
    [call] = executor._jobs.update.await_args_list
    again = AccountWait.from_record(call.kwargs["account_wait"])
    assert again is not None and again.since is not None and again.since > long_ago + timedelta(days=2), (
        "the new park counts from now: the loop already added the last one's time when it took the job back"
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_resume_across_the_total_fails_instead_of_waiting_again(
    db_with_migrations: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job with an hour left of its total is parked for that hour; the loop takes it back, adding the hour; its
    resume finds the account still spent, and the job fails with the account's code instead of waiting again."""
    _policy()
    from embry0.storage.repositories.jobs import JobsRepository

    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo="acme/app", task="t")
    await db_with_migrations.execute(
        "UPDATE jobs SET status = 'running', account_waited_seconds = $2 WHERE job_id = $1", job_id, 86400 - 3600
    )
    kept = {"account_wait": {"max_total_wait_seconds": 86400}}
    executor = _streaming_executor(monkeypatch, _Resolver(SPENT), AsyncMock())
    executor._jobs = jobs
    # The job's checkpoint keeps the account_wait it started with: a total of a day.
    graph = executor._select_workflow("issue-to-pr").compile()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values={"job_settings": kept}, next=()))

    with pytest.raises(JobParked) as parked:
        await executor._execute_workflow_stream(Command(resume={"answer": "csv"}), "iss-1", job_id)
    assert parked.value.wait.until <= datetime.now(UTC) + timedelta(hours=1), "parked for the hour left, no more"

    # The hour passes; the account-wait loop takes the job back and adds it.
    await db_with_migrations.execute(
        "UPDATE jobs SET account_wait = jsonb_set(account_wait, '{since}', to_jsonb($2::text)) WHERE job_id = $1",
        job_id,
        (datetime.now(UTC) - timedelta(seconds=3601)).isoformat(),
    )
    assert await jobs.claim_account_wait(job_id)
    row = await jobs.get(job_id)
    assert row is not None and row["account_waited_seconds"] >= 86400, (
        f"the loop adds the hour the job waited: {row and row['account_waited_seconds']}"
    )

    try:
        await executor._execute_workflow_stream(Command(resume={"answer": "csv"}), "iss-1", job_id)
    except JobParked:
        pytest.fail("a job past its account_wait.max_total_wait_seconds was parked to wait again")
    except Exception as exc:  # noqa: BLE001 - the failure is what the test reads
        assert classify_failure(exc=exc) == ErrorCode.PROVIDER_QUOTA_EXHAUSTED, repr(exc)
        assert "max_total_wait_seconds" in str(exc), str(exc)
    else:
        pytest.fail("a job past its total, with its account still spent, ran")
    after = await jobs.get(job_id)
    assert after is not None and after["account_wait"] is None and after["status"] == "running", (
        "nothing parked it again: its runner fails it"
    )


@pytest.mark.asyncio
async def test_a_row_that_cannot_be_read_counts_nothing_waited_but_one_too_deep_fails_the_run() -> None:
    """The read fails open, as the settings' does; a value too deep to read fails the run, as every read on its
    path does (#527)."""
    _policy()
    from embry0.services.issue_executor import IssueExecutor
    from embry0.storage.database import StoredValueTooDeep

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = AsyncMock()
    executor._jobs.get = AsyncMock(side_effect=ConnectionResetError("down"))
    policy = await _executor_run._job_wait_policy(executor, object(), {"repo": "acme/app"}, "job-1")
    assert policy.waited_seconds == 0 and policy.may_wait, "an unreadable row counts nothing waited"
    executor._jobs.get = AsyncMock(side_effect=StoredValueTooDeep("jobs"))
    with pytest.raises(StoredValueTooDeep):
        await _executor_run._job_wait_policy(executor, object(), {"repo": "acme/app"}, "job-1")
