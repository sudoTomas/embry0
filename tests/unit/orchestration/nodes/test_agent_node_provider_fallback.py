"""Provider fallback through run_agent_node (spec 2026-10-25): the model that runs, and its record."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.agents.executor import AgentOutput
from embry0.orchestration.nodes.agent import run_agent_node
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

_ROW = {
    "model_heavy": "claude-opus-5",
    "model_medium": "claude-sonnet-5",
    "model_light": "claude-haiku-4-5",
    "default_model": "",
    "disabled_models": [],
}
_PLAN = {"anthropic": {"model": "grok-4.7", "provider": "xai", "reason": "quota_exhausted", "until": None}}


class _ProviderRowDb:
    def __init__(self, order: list[str]) -> None:
        self._row = {**_ROW, "fallback_models": order}

    async def fetchrow(self, sql: str, *_args: Any) -> dict[str, Any] | None:
        return self._row if "provider_config" in sql else None


class _Traces:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> str:
        self.created.append(kwargs)
        return str(kwargs.get("trace_id"))

    async def finish(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _Pins:
    def __init__(self, pinned: set[str]) -> None:
        self._pinned = pinned

    async def job_pinned(self, job_id: str, provider: str) -> bool:
        return provider in self._pinned


async def _run(
    agent_type: str = "developer",
    *,
    order: list[str] | None = None,
    plan: dict[str, Any] | None = None,
    providers: tuple[str, ...] = ("anthropic", "xai"),
    state: dict[str, Any] | None = None,
    pinned: set[str] | None = None,
) -> tuple[str, _Traces, list[dict[str, Any]]]:
    captured: dict[str, Any] = {}
    traces = _Traces()
    seen: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> AgentOutput:
        captured["model"] = kwargs["config"]["model"]
        assert "fallback" not in kwargs["config"], "the sandbox is never sent the substitution"
        return AgentOutput(agent_type=agent_type, output="done", cost_usd=0.0)

    class _Runner:
        run = staticmethod(_capture)

    configurable: dict[str, Any] = {
        "db": _ProviderRowDb(["grok-4.7"] if order is None else order),
        "traces_repo": traces,
        "job_id": "job-1",
        "llm_providers": providers,
        "model_fallbacks": plan or {},
    }
    if pinned is not None:
        configurable["credential_resolver"] = _Pins(pinned)
    await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "job_id": "job-1", **(state or {})},
        agent_runner=_Runner(),
        agent_type=agent_type,
        prompt="do it",
        agent_definition=dict(BUILTIN_SEED[agent_type]),
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={"configurable": configurable},
        on_event=seen.append,
    )
    return str(captured["model"]), traces, seen


@pytest.mark.asyncio
async def test_the_planned_model_runs_and_the_substitution_is_recorded() -> None:
    model, traces, seen = await _run(plan=_PLAN)
    assert model == "grok-4.7"
    assert traces.created[0]["model"] == "grok-4.7", "the trace names the model that ran"
    assert traces.created[0]["fallback"] == {
        "instead_of": "claude-opus-5",
        "provider": "anthropic",
        "reason": "quota_exhausted",
        "until": None,
    }
    progress = [e for e in seen if e.get("type") == "progress"]
    assert len(progress) == 1 and progress[0]["node"] == "developer"
    assert progress[0]["message"] == (
        "developer runs on grok-4.7 instead of claude-opus-5: the anthropic account is paused (quota_exhausted)."
    )


@pytest.mark.asyncio
async def test_no_order_no_substitution_even_with_a_plan() -> None:
    model, traces, seen = await _run(order=[], plan=_PLAN)
    assert model == "claude-opus-5"
    assert "fallback" not in traces.created[0], "an unsubstituted run writes the trace exactly as before"
    assert not [e for e in seen if e.get("type") == "progress"]


@pytest.mark.asyncio
async def test_an_earlier_spent_run_moves_the_next_agent_to_a_provider_the_sandbox_holds() -> None:
    failed = {"agent_outputs": [{"provider": "anthropic", "error_code": "ERR_PROVIDER_QUOTA_EXHAUSTED"}]}
    model, traces, _seen = await _run("review", state=failed)
    assert model == "grok-4.7"
    assert traces.created[0]["fallback"]["reason"] == "quota_exhausted"
    held, _traces, _seen = await _run("review", state=failed, providers=("anthropic",))
    assert held == "claude-sonnet-5", "xai's key is not in the sandbox: nothing to move to"


@pytest.mark.asyncio
async def test_a_key_chosen_for_the_job_never_moves() -> None:
    failed = {"agent_outputs": [{"provider": "anthropic", "error_code": "ERR_PROVIDER_RATE_LIMITED"}]}
    model, traces, _seen = await _run("review", state=failed, pinned={"anthropic"})
    assert model == "claude-sonnet-5"
    assert "fallback" not in traces.created[0]


@pytest.mark.asyncio
async def test_a_model_the_job_names_never_moves() -> None:
    state = {"agent_models_override": {"*": "claude-haiku-4-5"}}
    model, traces, _seen = await _run(plan=_PLAN, state=state)
    assert model == "claude-haiku-4-5"
    assert "fallback" not in traces.created[0]


@pytest.mark.asyncio
async def test_a_resume_into_an_older_sandbox_uses_only_the_keys_it_holds() -> None:
    """The pre-check planned grok, but the container was created before the pause with anthropic's key only."""

    class _Manager:
        def last_llm_credentials(self, container_id: str) -> dict[str, Any]:
            return {"anthropic": object()} if container_id == "c1" else {}

    captured: dict[str, Any] = {}

    async def _capture(**kwargs: Any) -> AgentOutput:
        captured["model"] = kwargs["config"]["model"]
        return AgentOutput(agent_type="developer", output="done", cost_usd=0.0)

    class _Runner:
        run = staticmethod(_capture)

    configurable = {
        "db": _ProviderRowDb(["grok-4.7"]),
        "job_id": "job-1",
        "llm_providers": ("anthropic", "xai"),
        "model_fallbacks": _PLAN,
        "sandbox_manager": _Manager(),
    }
    await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "job_id": "job-1"},
        agent_runner=_Runner(),
        agent_type="developer",
        prompt="do it",
        agent_definition=dict(BUILTIN_SEED["developer"]),
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={"configurable": configurable},
    )
    assert captured["model"] == "claude-opus-5", "grok's key is not in that container: it runs as before"
