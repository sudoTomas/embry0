"""Spent account waits in the executor (spec 2026-10-25): park before the start or mid-job, and go on after."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.types import Command

from embry0.agents.fallback import Pause
from embry0.execution.sandbox_manager import SandboxReenrollError
from embry0.safety.error_codes import ErrorCode
from embry0.services import _executor_run
from embry0.services.account_waits import (
    AccountWait,
    AccountWaitInterrupt,
    AccountWaitRequired,
    JobParked,
)
from embry0.services.credentials.resolver import ResolvedCredential
from embry0.services.issue_executor import IssueExecutor

UNTIL = datetime.now(UTC) + timedelta(hours=15)
CONFIG = SimpleNamespace(credential_rate_limit_quarantine_seconds=900, credential_quota_quarantine_seconds=3600)


class _Resolver:
    def __init__(self, paused: dict[str, Pause], *, pinned: set[str] = frozenset()) -> None:
        self.paused = paused
        self.pinned = set(pinned)
        self.resolved: list[tuple[str, ...]] = []

    async def job_pinned(self, job_id: str, provider: str) -> bool:
        return provider in self.pinned

    async def pinned_pause(self, job_id: str, provider: str) -> tuple[bool, Pause | None]:
        return (True, self.paused.get(provider)) if provider in self.pinned else (False, None)

    async def llm_pause(self, repo: str, provider: str) -> Pause | None:
        return None if provider in self.pinned else self.paused.get(provider)

    async def job_uses_registry(self, job_id: str, provider: str) -> bool:
        return True

    async def resolve_llm_for_job(self, repo, providers, *, job_id, parent_job_id=None):
        providers = tuple(providers)
        self.resolved.append(providers)
        return {p: _cred(p) for p in providers}


def _cred(provider: str) -> ResolvedCredential:
    return ResolvedCredential(
        id=f"cred-{provider}",
        kind="llm_oauth" if provider == "anthropic" else "llm_api_key",
        provider=provider,
        host="h",
        label=provider,
        secret=f"secret-for-{provider}",
        git_author_name=None,
        git_author_email=None,
        source="registry",
        trace=(),
    )


class _ProviderRowDb:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def fetchrow(self, sql: str, *_args: Any) -> dict[str, Any]:
        return {
            "model_heavy": "claude-opus-5",
            "model_medium": "claude-sonnet-5",
            "model_light": "claude-haiku-4-5",
            "default_model": "",
            "disabled_models": [],
            "fallback_models": self.order,
        }


def _executor(resolver: _Resolver, order: list[str] | None = None) -> IssueExecutor:
    executor = IssueExecutor.__new__(IssueExecutor)
    executor._credential_resolver = resolver
    executor._db = _ProviderRowDb(order or [])
    executor._jobs = MagicMock()
    executor._jobs.get = AsyncMock(return_value=None)
    executor._jobs.update = AsyncMock()
    executor._traces = MagicMock()
    executor._traces.list_all = AsyncMock(return_value=([], 0))
    executor._config = CONFIG
    executor._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    return executor


@pytest.fixture(autouse=True)
def _claude_definitions(monkeypatch: pytest.MonkeyPatch) -> None:
    async def claude_only(_self: Any) -> list[dict[str, Any]]:
        return [{"model": "claude-opus-5"}, {"model": "claude-sonnet-5"}]

    monkeypatch.setattr(_executor_run, "_agent_definitions_for_precheck", claude_only)


SPENT = {"anthropic": Pause("anthropic", "quota_exhausted", UNTIL)}


async def _precheck(executor: IssueExecutor, state: dict[str, Any] | None = None) -> Any:
    return await _executor_run._precheck_llm_accounts(
        executor, "job-1", {"repo": "acme/app", "job_id": "job-1", **(state or {})}
    )


async def test_a_spent_main_provider_with_nothing_to_move_to_holds_the_job_before_it_starts() -> None:
    resolver = _Resolver(SPENT)
    with pytest.raises(AccountWaitRequired) as held:
        await _precheck(_executor(resolver))
    wait = held.value.wait
    assert (wait.provider, wait.reason, wait.until, wait.watch, wait.chosen) == (
        "anthropic",
        "quota_exhausted",
        UNTIL,
        True,
        False,
    )
    assert resolver.resolved == [], "no account resolved, no sandbox"


async def test_a_stand_in_in_the_fallback_order_lets_the_job_start() -> None:
    resolver = _Resolver(SPENT)
    _state, providers, _kinds, _ids, fallbacks = await _precheck(_executor(resolver, ["grok-4.7"]))
    assert fallbacks["anthropic"]["model"] == "grok-4.7" and "xai" in providers


async def test_a_model_a_person_chose_waits_even_with_a_stand_in() -> None:
    resolver = _Resolver(SPENT)
    with pytest.raises(AccountWaitRequired) as held:
        await _precheck(_executor(resolver, ["grok-4.7"]), {"agent_models_override": {"developer": "claude-opus-5"}})
    assert held.value.wait.chosen and held.value.wait.model == "claude-opus-5"


async def test_a_key_a_person_chose_that_is_quarantined_waits_instead_of_failing() -> None:
    resolver = _Resolver(SPENT, pinned={"anthropic"})
    with pytest.raises(AccountWaitRequired) as held:
        await _precheck(_executor(resolver, ["grok-4.7"]))
    assert held.value.wait.chosen and held.value.wait.watch


async def test_run_with_every_agent_on_another_provider_does_not_wait_for_the_main_one() -> None:
    resolver = _Resolver(SPENT)
    await _precheck(_executor(resolver), {"agent_models_override": {"*": "grok-4.7"}})


async def test_a_local_first_job_starts_while_its_local_model_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "custom")
    resolver = _Resolver(SPENT)
    await _precheck(_executor(resolver), {"local_first_model": "custom/qwen"})


async def test_a_ready_main_provider_starts_as_before() -> None:
    resolver = _Resolver({})
    _state, providers, _kinds, _ids, fallbacks = await _precheck(_executor(resolver))
    assert providers == ("anthropic",) and fallbacks == {}


# --- parking -----------------------------------------------------------------


class _Saver:
    async def __aenter__(self) -> _Saver:
        return self

    async def __aexit__(self, *_args: Any) -> bool:
        return False


def _streaming_executor(monkeypatch: pytest.MonkeyPatch, resolver: _Resolver, stream: Any) -> IssueExecutor:
    executor = _executor(resolver)
    executor._build_graph_config = lambda job_id, **kw: {"configurable": {"thread_id": job_id, **kw}}  # type: ignore[method-assign]
    graph = MagicMock()
    graph.aget_state = AsyncMock(return_value=SimpleNamespace(values={}, next=()))
    workflow = MagicMock()
    workflow.compile = MagicMock(return_value=graph)
    executor._select_workflow = lambda _pipeline: workflow  # type: ignore[method-assign]
    executor._process_stream = stream  # type: ignore[method-assign]
    executor._database_url = ""
    executor._sandbox = None
    monkeypatch.setattr(_executor_run, "checkpointer_context", lambda _url: _Saver())
    return executor


def _parked(executor: IssueExecutor) -> dict[str, Any]:
    [call] = [c for c in executor._jobs.update.await_args_list if c.kwargs.get("status") == "paused"]
    return call.kwargs["account_wait"]


async def test_a_held_fresh_job_is_parked_to_start_again(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = _streaming_executor(monkeypatch, _Resolver(SPENT), AsyncMock())
    with pytest.raises(JobParked):
        await executor._execute_workflow_stream({"repo": "acme/app", "job_id": "job-1"}, "iss-1", "job-1")
    record = _parked(executor)
    assert record["mode"] == "relaunch" and record["provider"] == "anthropic"
    executor._process_stream.assert_not_awaited()
    kinds = [c.args[1].get("type") for c in executor._broadcast_event.await_args_list]
    assert "interrupt" in kinds and "system" in kinds
    interrupt = next(
        c.args[1] for c in executor._broadcast_event.await_args_list if c.args[1].get("type") == "interrupt"
    )
    assert interrupt["reason"] == "account_wait" and interrupt["summary"]["until"] == record["until"]


async def test_a_held_resume_is_parked_with_the_answer_it_was_given(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = _streaming_executor(monkeypatch, _Resolver(SPENT), AsyncMock())
    executor._jobs.get = AsyncMock(return_value={"repo": "acme/app", "pipeline_config": {}})
    with pytest.raises(JobParked):
        await executor._execute_workflow_stream(Command(resume={"choice": "continue_retrying"}), "iss-1", "job-1")
    record = _parked(executor)
    assert record["mode"] == "resume" and record["resume_with"] == {"choice": "continue_retrying"}


async def test_a_run_that_stops_its_graph_parks_the_job_to_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    wait = AccountWait("anthropic", "quota_exhausted", UNTIL, model="claude-sonnet-5", agent="triage")

    async def stream(*_args: Any, **_kwargs: Any) -> Any:
        raise AccountWaitInterrupt(wait)

    executor = _streaming_executor(monkeypatch, _Resolver({}), stream)
    with pytest.raises(JobParked) as parked:
        await executor._execute_workflow_stream({"repo": "acme/app", "job_id": "job-1"}, "iss-1", "job-1")
    assert parked.value.wait.agent == "triage"
    assert _parked(executor)["mode"] == "resume"


async def test_only_pipelines_that_resume_let_a_run_park_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []
    executor = _streaming_executor(monkeypatch, _Resolver({}), AsyncMock(return_value=(None, False, None)))
    executor._build_graph_config = lambda job_id, **kw: seen.append(kw["account_waits"]) or {}  # type: ignore[method-assign]
    await executor._execute_workflow_stream({"repo": "acme/app"}, "iss-1", "job-1")
    await executor._execute_workflow_stream({"repo": "acme/app"}, "", "job-2", pipeline="pr-review")
    assert seen == [True, False]


# --- going on ------------------------------------------------------------------


def _row(mode: str, **extra: Any) -> dict[str, Any]:
    wait = AccountWait("anthropic", "quota_exhausted", UNTIL, mode=mode, **extra)  # type: ignore[arg-type]
    return {"job_id": "job-1", "issue_id": "iss-1", "launch": {"runner": "workflow"}, "account_wait": wait.record()}


async def test_a_resume_wait_goes_on_from_its_checkpoint() -> None:
    executor = IssueExecutor.__new__(IssueExecutor)
    executor._broadcast_event = AsyncMock()  # type: ignore[method-assign]
    executor.resume = AsyncMock()  # type: ignore[method-assign]
    executor._track_task = lambda coro, **_kw: coro  # type: ignore[method-assign,assignment]
    await executor.go_on_after_account_wait(_row("resume"))
    executor.resume.assert_awaited_once()
    args, kwargs = executor.resume.await_args
    assert args == ("iss-1", "job-1", None) and kwargs["account_wait"].provider == "anthropic"
    message = executor._broadcast_event.await_args.args[1]["message"]
    assert message == "Going on: the anthropic account is ready again."


async def test_a_relaunch_wait_starts_the_job_from_its_launch_record() -> None:
    executor = IssueExecutor.__new__(IssueExecutor)
    executor._relaunch = AsyncMock()  # type: ignore[method-assign]
    executor._track_task = lambda coro, **_kw: coro  # type: ignore[method-assign,assignment]
    await executor.go_on_after_account_wait(_row("relaunch"))
    executor._relaunch.assert_awaited_once()
    assert executor._relaunch.await_args.args == ("job-1", {"runner": "workflow"})


def _resuming_executor(stream: Any) -> IssueExecutor:
    executor = IssueExecutor.__new__(IssueExecutor)
    executor._jobs = MagicMock()
    executor._jobs.get = AsyncMock(return_value={"status": "running"})
    executor._jobs.update = AsyncMock()
    executor._issues = MagicMock()
    executor._issues.update = AsyncMock()
    executor._acquire_run_slot = AsyncMock(return_value=None)  # type: ignore[method-assign]
    executor._execute_workflow_stream = stream  # type: ignore[method-assign]
    executor._handle_workflow_result = AsyncMock()  # type: ignore[method-assign]
    executor._cleanup_sandbox = AsyncMock()  # type: ignore[method-assign]
    executor._post_tracker_outcome = AsyncMock()  # type: ignore[method-assign]
    return executor


async def test_going_on_from_a_checkpoint_streams_none_and_keeps_the_claimed_status() -> None:
    stream = AsyncMock(return_value=({"current_stage": "completed"}, False, None))
    executor = _resuming_executor(stream)
    wait = AccountWait("anthropic", "quota_exhausted", UNTIL)
    await executor.resume("iss-1", "job-1", None, account_wait=wait)
    assert stream.await_args.args[0] is None, "from the last checkpoint: the stopped node runs again"
    assert not [c for c in executor._jobs.update.await_args_list if c.kwargs.get("status") == "running"]
    executor._handle_workflow_result.assert_awaited_once()


async def test_a_sandbox_the_proxies_will_not_take_back_parks_the_job_again() -> None:
    stream = AsyncMock(side_effect=SandboxReenrollError("proxy restarting", error_code=ErrorCode.SANDBOX_INIT))
    executor = _resuming_executor(stream)
    await executor.resume("iss-1", "job-1", None, account_wait=AccountWait("anthropic", "quota_exhausted", UNTIL))
    [call] = executor._jobs.update.await_args_list
    assert call.kwargs["status"] == "paused"
    again = AccountWait.from_record(call.kwargs["account_wait"])
    assert again is not None and again.until <= datetime.now(UTC) + timedelta(minutes=6) and not again.watch
