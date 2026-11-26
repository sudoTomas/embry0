"""Whether a job waits for a spent account, and how long, is its account_wait settings, kept from its start (#173).

Before the section a job always waited, a wait's end was cut at eight days
and a forced local run with no slot tried again after five minutes:
constants. The settings document's ``account_wait`` section says the same
by default, and an organization's layer or a repository's may set every key.
A job reads it for its repository when it starts and keeps it through every
pause and resume, so a change reaches the next job that starts.
``enabled: false``: the job still moves to a ready model of the fallback
order, and with none it fails at once with the account's own error code, as
it did before waits. The section is imported inside each test, so a tree
without it fails each one with its own message.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from embry0.agents.fallback import Pause
from embry0.orchestration.state import JobState
from embry0.safety.error_codes import ErrorCode, classify_failure
from embry0.services import _executor_run
from embry0.services.account_waits import AccountWait, AccountWaitRequired, JobParked
from embry0.services.issue_executor import IssueExecutor
from embry0.settings.document import DocumentInvalid, check, load_yaml
from embry0.settings.sections import CATALOG
from tests.unit.services.test_job_settings import WIDGETS, _start, _Store
from tests.unit.services.test_spent_account_park import (
    SPENT,
    UNTIL,
    _claude_definitions,  # noqa: F401 - autouse: the job's agents run on Claude, as in that module
    _executor,
    _precheck,
    _Resolver,
    _streaming_executor,
)

GIZMOS, GADGETS = "acme/gizmos", "globex/gadgets"
DEFAULTS = {
    "enabled": True,
    "max_wait_seconds": 8 * 24 * 3600,
    "max_total_wait_seconds": 30 * 24 * 3600,
    "local_busy_retry_seconds": 300,
}


def _section() -> Any:
    found = CATALOG.get("account_wait")
    assert found is not None, "the settings document has no account_wait section: whether a job waits is code"
    return found


def _kept(state: dict[str, Any]) -> dict[str, Any]:
    """The account_wait values ``state``'s job keeps, as plain JSON."""
    from embry0.services.job_settings import job_section

    return job_section(state, _section()).model_dump(mode="json")


def _stored(document: dict[str, Any], layer: str) -> Any:
    _section()
    try:
        return check(CATALOG, document, stored={}, layer=layer).data
    except DocumentInvalid as exc:
        return [(problem.loc, problem.msg) for problem in exc.problems]


# --- the section -----------------------------------------------------------------


@pytest.mark.parametrize("layer", ["deployment", "organization:acme", f"repository:{WIDGETS}"])
def test_every_layer_may_set_every_key(layer: str) -> None:
    written = (
        "account_wait:\n  enabled: false\n  max_wait_seconds: 3600\n  max_total_wait_seconds: 7200\n"
        "  local_busy_retry_seconds: 120\n"
    )
    data = _stored(load_yaml(written), layer)
    values = {
        "enabled": False,
        "max_wait_seconds": 3600,
        "max_total_wait_seconds": 7200,
        "local_busy_retry_seconds": 120,
    }
    assert data == {"account_wait": values}, f"{layer}: stored as written: {data}"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_wait_seconds", 59),
        ("max_wait_seconds", 30 * 24 * 3600 + 1),
        ("local_busy_retry_seconds", 59),
        ("local_busy_retry_seconds", 24 * 3600 + 1),
        ("max_total_wait_seconds", 3599),
        ("max_total_wait_seconds", 90 * 24 * 3600 + 1),
        ("max_wait_seconds", "8d"),
        ("enabled", "no"),
    ],
)
def test_a_time_out_of_bounds_or_a_value_of_the_wrong_kind_is_refused(key: str, value: Any) -> None:
    refused = _stored({"account_wait": {key: value}}, "deployment")
    assert isinstance(refused, list) and any(key in str(loc) for loc, _msg in refused), (
        f"account_wait.{key}: {value!r} must be refused, naming the key: {refused}"
    )


def test_nothing_written_is_what_every_job_had_before_the_section() -> None:
    assert _section().defaults().model_dump(mode="json") == DEFAULTS, (
        "the defaults are the old constants: a job waits, a wait's end is cut at eight days, and a forced local run "
        "with no slot tries again after five minutes; and all of a job's waits together are bounded at thirty days"
    )


# --- read when the job starts, kept to its end -------------------------------------


@pytest.mark.asyncio
async def test_each_job_reads_its_repository_s_layer_else_its_organization_s_else_the_deployment_s() -> None:
    _section()
    store = _Store(
        {"account_wait": {"max_wait_seconds": 7200}},
        **{
            "organization:acme": {"account_wait": {"enabled": False, "max_total_wait_seconds": 86400}},
            f"repository:{WIDGETS}": {"account_wait": {"enabled": True, "local_busy_retry_seconds": 900}},
        },
    )
    seen = {repo: _kept(await _start(store, repo)) for repo in (WIDGETS, GIZMOS, GADGETS)}
    assert seen == {
        WIDGETS: {
            **DEFAULTS,
            "max_wait_seconds": 7200,
            "max_total_wait_seconds": 86400,
            "local_busy_retry_seconds": 900,
        },
        GIZMOS: {**DEFAULTS, "enabled": False, "max_wait_seconds": 7200, "max_total_wait_seconds": 86400},
        GADGETS: {**DEFAULTS, "max_wait_seconds": 7200},
    }, f"each job's values must be its repository's layer, else its organization's, else the deployment's: {seen}"


@pytest.mark.asyncio
async def test_a_job_going_on_after_a_pause_keeps_the_values_it_started_with(monkeypatch: pytest.MonkeyPatch) -> None:
    _section()
    saver = InMemorySaver()

    @asynccontextmanager
    async def _checkpoints(_url: str) -> Any:
        yield saver

    monkeypatch.setattr(_executor_run, "checkpointer_context", _checkpoints)

    def ask(_state: JobState) -> dict[str, Any]:
        interrupt({"question": "which export?"})
        return {}

    builder = StateGraph(JobState)
    builder.add_node("ask", ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)

    class _Workflow:
        def compile(self, config: dict[str, Any]) -> Any:
            return builder.compile(checkpointer=config["checkpointer"])

    store = _Store(**{f"repository:{WIDGETS}": {"account_wait": {"enabled": False, "max_wait_seconds": 3600}}})
    started = await _start(store, WIDGETS)
    await builder.compile(checkpointer=saver).ainvoke(started, {"configurable": {"thread_id": "job-1"}})
    store.write()  # the layer is taken out while the job waits for its answer

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._database_url = "postgresql://unused"
    for how, going_on in (("an answer", Command(resume={"answer": "csv"})), ("a restart", None)):
        kept = (await _executor_run._job_wait_policy(executor, _Workflow(), going_on, "job-1")).settings
        assert kept.model_dump(mode="json") == {**DEFAULTS, "enabled": False, "max_wait_seconds": 3600}, (
            f"after {how} the job reads the values it started with, off its checkpoint, not the document's now: {kept}"
        )
    assert _kept(await _start(store, WIDGETS, job_id="job-2")) == DEFAULTS, "the next job reads the document now"


@pytest.mark.asyncio
async def test_a_checkpoint_that_cannot_be_read_gives_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _section()

    @asynccontextmanager
    async def _down(_url: str) -> Any:
        raise ConnectionRefusedError("checkpoints are down")
        yield  # pragma: no cover

    monkeypatch.setattr(_executor_run, "checkpointer_context", _down)
    executor = IssueExecutor.__new__(IssueExecutor)
    executor._database_url = "postgresql://unused"
    kept = (await _executor_run._job_wait_policy(executor, object(), Command(resume=None), "job-1")).settings
    assert kept.model_dump(mode="json") == DEFAULTS, "a job is never failed for want of its settings"


# --- the account pre-check ------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "code"),
    [("quota_exhausted", ErrorCode.PROVIDER_QUOTA_EXHAUSTED), ("rate_limited", ErrorCode.PROVIDER_RATE_LIMITED)],
)
@pytest.mark.asyncio
async def test_a_job_that_may_not_wait_fails_before_it_starts_with_the_account_s_code(
    reason: str, code: ErrorCode
) -> None:
    _section()
    resolver = _Resolver({"anthropic": Pause("anthropic", reason, UNTIL)})
    try:
        await _precheck(_executor(resolver), enabled=False)
    except AccountWaitRequired:
        pytest.fail("a job whose account_wait.enabled is false was held to wait")
    except Exception as exc:  # noqa: BLE001 - the failure is what the test reads
        assert classify_failure(exc=exc) == code, f"the job fails with the account's own code: {exc!r}"
        assert "account_wait.enabled is false" in str(exc), f"the failure says why the job did not wait: {exc}"
    else:
        pytest.fail("a job whose account is spent, with nowhere to move and no wait, started")
    assert resolver.resolved == [], "it fails before any account is resolved or any sandbox exists"


@pytest.mark.asyncio
async def test_a_job_that_may_not_wait_still_starts_on_a_ready_model_of_the_fallback_order() -> None:
    _section()
    _state, providers, _kinds, _ids, fallbacks = await _precheck(
        _executor(_Resolver(SPENT), order=["grok-4.7"]), enabled=False
    )
    assert fallbacks["anthropic"]["model"] == "grok-4.7" and "xai" in providers, fallbacks


@pytest.mark.asyncio
async def test_the_job_s_max_wait_cuts_the_wait_it_is_held_in() -> None:
    _section()
    before = datetime.now(UTC)
    with pytest.raises(AccountWaitRequired) as held:
        await _precheck(_executor(_Resolver(SPENT)), max_wait_seconds=3600)
    until = held.value.wait.until
    assert before + timedelta(seconds=3600) <= until <= datetime.now(UTC) + timedelta(seconds=3600) < UNTIL, (
        f"a reset 15 hours out is cut to the job's max_wait_seconds: {until - before}"
    )


# --- the run: the job's values reach the pre-check and every run ---------------------


def _state_with(**account_wait: Any) -> dict[str, Any]:
    return {"repo": "acme/app", "job_id": "job-1", "job_settings": {"account_wait": {**DEFAULTS, **account_wait}}}


@pytest.mark.asyncio
async def test_a_fresh_run_hands_its_job_s_values_to_every_run(monkeypatch: pytest.MonkeyPatch) -> None:
    _section()
    executor = _streaming_executor(monkeypatch, _Resolver({}), AsyncMock(return_value=(None, False, None)))
    await executor._execute_workflow_stream(_state_with(max_wait_seconds=7200), "iss-1", "job-1")
    configurable = executor._process_stream.await_args.args[2]["configurable"]
    carried = configurable.get("account_wait")
    assert carried is not None and carried.settings.model_dump(mode="json") == {**DEFAULTS, "max_wait_seconds": 7200}, (
        f"every run's graph config carries the job's own account_wait values: {carried}"
    )


def test_the_graph_config_carries_the_values_it_is_given_and_the_defaults_without() -> None:
    settings = _section().validate({"enabled": False, "max_wait_seconds": 600})
    from embry0.services.account_waits import WaitPolicy, job_wait_policy
    from tests.unit.services.test_issue_executor_graph_config import _make_executor

    off = WaitPolicy(settings, 900)
    executor = _make_executor()
    configurable = executor._build_graph_config("job-1", account_wait=off)["configurable"]
    assert job_wait_policy(configurable) is off, "a run reads the job's values off its graph config"
    bare = job_wait_policy(executor._build_graph_config("job-1")["configurable"])
    assert (bare.settings.model_dump(mode="json"), bare.waited_seconds) == (DEFAULTS, 0), (
        "a config built without them reads the defaults and nothing waited"
    )


@pytest.mark.asyncio
async def test_a_fresh_job_that_may_not_wait_fails_and_is_not_parked(monkeypatch: pytest.MonkeyPatch) -> None:
    _section()
    executor = _streaming_executor(monkeypatch, _Resolver(SPENT), AsyncMock())
    try:
        await executor._execute_workflow_stream(_state_with(enabled=False), "iss-1", "job-1")
    except JobParked:
        pytest.fail("a job whose account_wait.enabled is false was parked")
    except Exception as exc:  # noqa: BLE001 - the failure is what the test reads
        assert classify_failure(exc=exc) == ErrorCode.PROVIDER_QUOTA_EXHAUSTED, repr(exc)
    else:
        pytest.fail("a job whose account is spent, with nowhere to move and no wait, ran")
    assert not [c for c in executor._jobs.update.await_args_list if c.kwargs.get("status") == "paused"]
    executor._process_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_resume_reads_its_job_s_values_off_the_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _section()
    executor = _streaming_executor(monkeypatch, _Resolver(SPENT), AsyncMock())
    executor._jobs.get = AsyncMock(return_value={"repo": "acme/app", "pipeline_config": {}})
    graph = executor._select_workflow("issue-to-pr").compile()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values=_state_with(enabled=False), next=()))
    try:
        await executor._execute_workflow_stream(Command(resume={"choice": "continue_retrying"}), "iss-1", "job-1")
    except JobParked:
        pytest.fail("a resumed job that started with account_wait.enabled false was parked: it read the defaults")
    except Exception as exc:  # noqa: BLE001 - the failure is what the test reads
        assert classify_failure(exc=exc) == ErrorCode.PROVIDER_QUOTA_EXHAUSTED, repr(exc)
    else:
        pytest.fail("a resumed job whose account is spent, with nowhere to move and no wait, ran")


@pytest.mark.asyncio
async def test_the_watcher_asks_whether_a_job_would_be_held_whatever_the_repository_says() -> None:
    """A job that would wait and one that would fail at once are both a job the watcher does not start."""
    _section()
    wait = await _executor(_Resolver(SPENT)).account_wait_before_start("acme/app", {})
    assert isinstance(wait, AccountWait) and wait.provider == "anthropic"


# --- the runners: what each job keeps, and the code it fails with ----------------------

RUNNERS = ["issue_to_pr", "qa", "onboard", "pr_review"]


def _runner_executor(jobs: Any, store: Any) -> IssueExecutor:
    from tests.unit.services.test_budget_gate_before_running import _executor as gated

    executor = gated(jobs, allowed=True)
    executor._settings = store
    executor._traces.total_cost_for_job = AsyncMock(return_value=0.0)
    return executor


async def _start_runner(executor: IssueExecutor, runner: str, job_id: str) -> None:
    from tests.unit.services.test_budget_gate_before_running import _run

    with patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()):
        await _run(executor, runner, job_id)


@pytest.mark.requires_postgres
@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.asyncio
async def test_every_runner_starts_its_job_with_its_repository_s_account_wait(
    db_with_migrations: Any, runner: str
) -> None:
    from embry0.storage.repositories.jobs import JobsRepository

    _section()
    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo=WIDGETS, task="t")
    store = _Store(**{f"repository:{WIDGETS}": {"account_wait": {"enabled": False, "local_busy_retry_seconds": 600}}})
    executor = _runner_executor(jobs, store)
    started: list[Any] = []

    async def _stream(input_value: Any, *_args: Any, **_kwargs: Any) -> tuple[None, bool, None]:
        started.append(input_value)
        return None, False, None

    executor._execute_workflow_stream = _stream  # type: ignore[method-assign]
    executor._issues.get = AsyncMock(return_value=None)
    if runner == "issue_to_pr":
        executor._run_workflow = _patched_issue_runner(executor)  # type: ignore[method-assign]
    await _start_runner(executor, runner, job_id)
    assert started, f"{runner}: the runner never reached its graph"
    assert _kept(started[0]) == {**DEFAULTS, "enabled": False, "local_busy_retry_seconds": 600}, (
        f"{runner}: the job keeps its repository's account_wait from its start: {started[0].get('job_settings')}"
    )


def _patched_issue_runner(executor: IssueExecutor) -> Any:
    """``_run_workflow`` for an issue on WIDGETS: the budget harness starts one with no repository."""
    real = executor._run_workflow

    async def run(issue_id: Any, job_id: str, issue: dict[str, Any], **kwargs: Any) -> Any:
        return await real(issue_id, job_id, {**issue, "repo": WIDGETS}, **kwargs)

    return run


@pytest.mark.requires_postgres
@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.asyncio
async def test_a_job_that_may_not_wait_ends_failed_with_the_account_s_code(
    db_with_migrations: Any, runner: str
) -> None:
    _section()
    from embry0.services.account_waits import AccountSpent
    from embry0.storage.repositories.jobs import JobsRepository
    from embry0.storage.schemas import JobStatus

    jobs = JobsRepository(db_with_migrations)
    job_id = await jobs.create(repo=WIDGETS, task="t")
    executor = _runner_executor(jobs, _Store())
    spent = AccountWait("anthropic", "rate_limited", UNTIL, model="claude-sonnet-5")
    from embry0.services.account_waits import WaitPolicy

    policy = WaitPolicy(_section().validate({"enabled": False}))
    executor._execute_workflow_stream = AsyncMock(side_effect=AccountSpent(spent, policy))  # type: ignore[method-assign]
    await _start_runner(executor, runner, job_id)
    row = await jobs.get(job_id)
    assert row is not None and row["status"] == JobStatus.FAILED, f"{runner}: {row and row['status']}"
    assert row["error_code"] == ErrorCode.PROVIDER_RATE_LIMITED.value, (
        f"{runner}: the job's code is the account's own, as before waits: {row['error_code']}"
    )
    assert row.get("account_wait") is None, f"{runner}: nothing waits"
