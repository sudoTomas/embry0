"""Spent account waits through run_agent_node (spec 2026-10-25): move at once, or park the job."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.agents.fallback import Pause
from embry0.execution.agent_runner import AgentOutput
from embry0.orchestration.nodes.agent import run_agent_node
from embry0.services.account_waits import LOCAL_BUSY, AccountWaitInterrupt
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

_ROW = {
    "model_heavy": "claude-opus-5",
    "model_medium": "claude-sonnet-5",
    "model_light": "claude-haiku-4-5",
    "default_model": "",
    "disabled_models": [],
}
RESET = datetime.now(UTC) + timedelta(hours=15)


class _ProviderRowDb:
    def __init__(self, order: list[str]) -> None:
        self._row = {**_ROW, "fallback_models": order}

    async def fetchrow(self, sql: str, *_args: Any) -> dict[str, Any] | None:
        return self._row if "provider_config" in sql else None


class _Traces:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.finished: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> str:
        self.created.append(kwargs)
        return str(kwargs.get("trace_id"))

    async def finish(self, trace_id: str, **kwargs: Any) -> None:
        self.finished.append({"trace_id": trace_id, **kwargs})

    async def list_all(self, **_kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        return [], 0


class _Health:
    def __init__(self) -> None:
        self.reports: list[tuple[str, str, str | None, Any]] = []

    async def report_job(self, job_id: str, code: str, provider: str | None, **kwargs: Any) -> list[str]:
        self.reports.append((job_id, code, provider, kwargs.get("resets_at")))
        return ["cred-1"]


class _Resolver:
    def __init__(
        self,
        *,
        paused: dict[str, Pause] | None = None,
        pinned: set[str] | None = None,
        uses_registry: bool = True,
    ) -> None:
        self.paused = paused or {}
        self.pinned = pinned or set()
        self.uses_registry = uses_registry

    async def job_pinned(self, job_id: str, provider: str) -> bool:
        return provider in self.pinned

    async def pinned_pause(self, job_id: str, provider: str) -> tuple[bool, Pause | None]:
        if provider in self.pinned:
            return True, self.paused.get(provider)
        return False, None

    async def llm_pause(self, repo: str, provider: str) -> Pause | None:
        return None if provider in self.pinned else self.paused.get(provider)

    async def job_uses_registry(self, job_id: str, provider: str) -> bool:
        return self.uses_registry


def _spent(agent_type: str) -> AgentOutput:
    out = AgentOutput(
        agent_type=agent_type,
        is_error=True,
        error_message="You've hit your weekly limit · resets 12am (UTC)",
        error_code="ERR_PROVIDER_QUOTA_EXHAUSTED",
    )
    out.provider_resets_at = RESET
    return out


async def _run(
    agent_type: str = "triage",
    *,
    order: list[str] | None = None,
    providers: tuple[str, ...] = ("anthropic", "xai"),
    results: list[AgentOutput] | None = None,
    resolver: _Resolver | None = None,
    waits: bool = True,
    state: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str], _Traces, _Health, list[dict[str, Any]]]:
    ran: list[str] = []
    queue = list(results or [AgentOutput(agent_type=agent_type, output="done")])
    traces, health, seen = _Traces(), _Health(), []

    async def _capture(**kwargs: Any) -> AgentOutput:
        ran.append(kwargs["config"]["model"])
        return queue.pop(0) if queue else AgentOutput(agent_type=agent_type, output="done")

    class _Runner:
        run = staticmethod(_capture)

    configurable: dict[str, Any] = {
        "db": _ProviderRowDb([] if order is None else order),
        "traces_repo": traces,
        "job_id": "job-1",
        "llm_providers": providers,
        "model_fallbacks": {},
        "credential_resolver": resolver or _Resolver(),
        "credential_health": health,
        "account_waits": waits,
    }
    updates = await run_agent_node(
        state={
            "sandbox_container_id": "c1",
            "total_cost_usd": 0.0,
            "job_id": "job-1",
            "repo": "acme/app",
            **(state or {}),
        },
        agent_runner=_Runner(),
        agent_type=agent_type,
        prompt="do it",
        agent_definition=dict(BUILTIN_SEED[agent_type]),
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={"configurable": configurable},
        on_event=seen.append,
    )
    return updates, ran, traces, health, seen


@pytest.mark.asyncio
async def test_a_run_that_finds_its_account_spent_runs_again_on_the_next_ready_model() -> None:
    updates, ran, traces, health, seen = await _run(order=["grok-4.7"], results=[_spent("triage")])
    assert ran == ["claude-sonnet-5", "grok-4.7"], "the same run, again, at once"
    assert health.reports == [("job-1", "ERR_PROVIDER_QUOTA_EXHAUSTED", "anthropic", RESET)], "quarantined now"
    outputs = updates["agent_outputs"]
    assert [o.get("error_code") for o in outputs] == ["ERR_PROVIDER_QUOTA_EXHAUSTED", None]
    assert not outputs[-1]["is_error"] and outputs[-1]["provider"] == "xai", "the node reads the last one"
    assert "errors" not in updates, "the spent attempt's error is not the node's"
    assert traces.created[1]["fallback"]["instead_of"] == "claude-sonnet-5"
    assert traces.finished[0]["error_code"] == "ERR_PROVIDER_QUOTA_EXHAUSTED", "the failed attempt's trace stays"
    assert any("runs again on the next ready model" in str(e.get("message")) for e in seen)


@pytest.mark.asyncio
async def test_with_nowhere_to_move_the_job_waits_for_the_reset() -> None:
    with pytest.raises(AccountWaitInterrupt) as stopped:
        await _run(order=[], results=[_spent("triage")])
    wait = stopped.value.wait
    assert (wait.provider, wait.reason, wait.agent, wait.model) == (
        "anthropic",
        "quota_exhausted",
        "triage",
        "claude-sonnet-5",
    )
    assert wait.until == RESET and wait.watch, "a registry account: the loop may go on once it is usable"
    assert not wait.chosen


@pytest.mark.asyncio
async def test_a_pipeline_that_cannot_resume_keeps_the_failure() -> None:
    updates, ran, _traces, health, _seen = await _run(order=[], results=[_spent("triage")], waits=False)
    assert ran == ["claude-sonnet-5"], "not run again on the same spent account"
    assert updates["agent_outputs"][-1]["error_code"] == "ERR_PROVIDER_QUOTA_EXHAUSTED"
    assert health.reports, "still quarantined at once"


@pytest.mark.asyncio
async def test_a_model_a_person_chose_waits_instead_of_moving() -> None:
    with pytest.raises(AccountWaitInterrupt) as stopped:
        await _run(
            order=["grok-4.7"],
            results=[_spent("triage")],
            state={"agent_models_override": {"triage": "claude-sonnet-5"}},
        )
    assert stopped.value.wait.chosen


@pytest.mark.asyncio
async def test_a_chosen_key_waits_instead_of_moving() -> None:
    with pytest.raises(AccountWaitInterrupt) as stopped:
        await _run(order=["grok-4.7"], results=[_spent("triage")], resolver=_Resolver(pinned={"anthropic"}))
    assert stopped.value.wait.chosen and stopped.value.wait.watch


@pytest.mark.asyncio
async def test_a_run_never_starts_on_an_account_already_known_spent() -> None:
    quarantined = _Resolver(paused={"anthropic": Pause("anthropic", "quota_exhausted", RESET)})
    with pytest.raises(AccountWaitInterrupt) as stopped:
        await _run(order=[], resolver=quarantined)
    assert stopped.value.wait.until == RESET
    _updates, ran, _traces, health, _seen = await _run(order=["grok-4.7"], resolver=quarantined)
    assert ran == ["grok-4.7"], "found paused since the plan was made: it moves like the plan would"
    assert health.reports == []


@pytest.mark.asyncio
async def test_a_pause_with_no_end_runs_as_before() -> None:
    missing = _Resolver(paused={"anthropic": Pause("anthropic", "missing")})
    updates, ran, _traces, _health, _seen = await _run(order=[], resolver=missing)
    assert ran == ["claude-sonnet-5"] and not updates["agent_outputs"][-1]["is_error"]


@pytest.mark.asyncio
async def test_a_forced_local_run_with_no_slot_waits_instead_of_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    import embry0.orchestration.nodes.agent as agent_mod

    class _Slots:
        async def acquire(self, **_kwargs: Any) -> None:
            return None

        async def release(self, _lease: str) -> None:
            return None

    async def _slots(_config: Any) -> tuple[_Slots, int]:
        return _Slots(), 1

    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "custom")
    monkeypatch.setattr(agent_mod, "_local_slots", _slots)
    monkeypatch.setattr(agent_mod, "_slot_wait_seconds", lambda _config: 0.0)
    with pytest.raises(AccountWaitInterrupt) as stopped:
        await _run(state={"agent_models_override": {"triage": "custom/qwen"}}, providers=("anthropic", "custom"))
    assert stopped.value.wait.reason == LOCAL_BUSY and stopped.value.wait.provider == "custom"
