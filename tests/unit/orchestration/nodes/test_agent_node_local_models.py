"""Local models through run_agent_node: local-first while a slot is free, and the one-at-a-time slot."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from embry0.agents.executor import AgentOutput
from embry0.orchestration.nodes.agent import run_agent_node
from embry0.safety.error_codes import ErrorCode
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED

_LOCAL = "custom/qwen"
_ROW = {
    "model_heavy": "claude-opus-5",
    "model_medium": "claude-sonnet-5",
    "model_light": "claude-haiku-4-5",
    "default_model": "",
    "disabled_models": [],
    "fallback_models": [],
    "local_max_runs": 1,
}


@pytest.fixture(autouse=True)
def _custom_enabled(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "custom")


class _Db:
    async def fetchrow(self, sql: str, *_args: Any) -> dict[str, Any] | None:
        return _ROW if "provider_config" in sql else None


class _Traces:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> str:
        self.created.append(kwargs)
        return str(kwargs.get("trace_id"))

    async def finish(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _Slots:
    """A fake of LocalSlots: ``free`` says whether a slot is free, now and after any wait."""

    def __init__(self, *, free: bool = True) -> None:
        self.free = free
        self.taken: list[dict[str, Any]] = []
        self.released: list[str] = []
        self.waited = False

    async def try_acquire(self, **kwargs: Any) -> str | None:
        if not self.free:
            return None
        self.taken.append(kwargs)
        return f"lease-{len(self.taken)}"

    async def acquire(self, *, wait_seconds: float, on_wait: Any = None, **kwargs: Any) -> str | None:
        if not self.free:
            self.waited = True
            if on_wait is not None:
                on_wait()
            return None
        return await self.try_acquire(**kwargs)

    async def release(self, lease_id: str) -> None:
        self.released.append(lease_id)


async def _run(
    *,
    slots: _Slots,
    state: dict[str, Any] | None = None,
    providers: tuple[str, ...] = ("anthropic", "custom"),
    runner_error: BaseException | None = None,
) -> tuple[str | None, dict[str, Any], _Traces, list[dict[str, Any]]]:
    captured: dict[str, Any] = {}
    traces = _Traces()
    seen: list[dict[str, Any]] = []

    async def _capture(**kwargs: Any) -> AgentOutput:
        captured["model"] = kwargs["config"]["model"]
        assert "local_first" not in kwargs["config"], "the sandbox is never sent the preference"
        if runner_error is not None:
            raise runner_error
        return AgentOutput(agent_type="analysis", output="done", cost_usd=0.0)

    class _Runner:
        run = staticmethod(_capture)

    update = await run_agent_node(
        state={"sandbox_container_id": "c1", "total_cost_usd": 0.0, "job_id": "job-1", **(state or {})},
        agent_runner=_Runner(),
        agent_type="analysis",
        prompt="read the logs",
        agent_definition=dict(BUILTIN_SEED["analysis"]),
        credentials={"api_key": "", "oauth_token": "oauth-xyz"},
        config={
            "configurable": {
                "db": _Db(),
                "traces_repo": traces,
                "job_id": "job-1",
                "llm_providers": providers,
                "local_slots": slots,
            }
        },
        on_event=seen.append,
    )
    return captured.get("model"), update, traces, seen


def _progress(seen: list[dict[str, Any]]) -> list[str]:
    return [e["message"] for e in seen if e.get("type") == "progress"]


@pytest.mark.asyncio
async def test_with_a_slot_free_the_local_first_job_runs_on_the_local_model() -> None:
    slots = _Slots()
    model, _, traces, seen = await _run(slots=slots, state={"local_first_model": _LOCAL})
    cloud = BUILTIN_SEED["analysis"]["model"]
    assert model == _LOCAL
    assert traces.created[0]["model"] == _LOCAL, "the trace names the model that ran"
    assert _progress(seen) == [
        f"analysis runs on the local model {_LOCAL} instead of {cloud}: this job prefers it while a slot is free."
    ]
    assert slots.taken[0]["cap"] == 1 and slots.taken[0]["model"] == _LOCAL
    assert slots.released == ["lease-1"], "the slot comes back when the run ends"


@pytest.mark.asyncio
async def test_with_every_slot_taken_it_runs_on_its_own_model_at_once() -> None:
    slots = _Slots(free=False)
    model, _, traces, seen = await _run(slots=slots, state={"local_first_model": _LOCAL})
    cloud = BUILTIN_SEED["analysis"]["model"]
    assert model == cloud
    assert traces.created[0]["model"] == cloud
    assert not slots.waited, "a preference never waits"
    assert _progress(seen) == [f"analysis runs on {cloud}: every slot of the local model {_LOCAL} is taken."]
    assert slots.released == []


@pytest.mark.asyncio
async def test_a_model_the_job_names_is_never_moved_to_the_local_model() -> None:
    slots = _Slots()
    model, *_ = await _run(
        slots=slots, state={"local_first_model": _LOCAL, "agent_models_override": {"*": "claude-opus-5"}}
    )
    assert model == "claude-opus-5"
    assert slots.taken == []


@pytest.mark.asyncio
async def test_no_preference_when_the_sandbox_has_no_route_to_the_local_server() -> None:
    slots = _Slots()
    model, *_ = await _run(slots=slots, state={"local_first_model": _LOCAL}, providers=("anthropic",))
    assert model == BUILTIN_SEED["analysis"]["model"]
    assert slots.taken == []


@pytest.mark.asyncio
async def test_a_run_that_must_use_the_local_model_waits_then_fails_without_running() -> None:
    slots = _Slots(free=False)
    model, update, traces, seen = await _run(slots=slots, state={"agent_models_override": {"*": _LOCAL}})
    assert model is None, "nothing ran"
    assert slots.waited
    assert _progress(seen) == [f"analysis waits for a free slot on the local model {_LOCAL}."]
    entry = update["agent_outputs"][0]
    assert (entry["error_code"], entry["provider"]) == (ErrorCode.LOCAL_MODEL_BUSY.value, "custom")
    assert update["error_code"] == ErrorCode.LOCAL_MODEL_BUSY.value
    assert traces.created == []


@pytest.mark.asyncio
async def test_a_run_that_must_use_the_local_model_takes_a_free_slot() -> None:
    slots = _Slots()
    model, *_ = await _run(slots=slots, state={"agent_models_override": {"*": _LOCAL}})
    assert model == _LOCAL and slots.released == ["lease-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RuntimeError("the exec died"), asyncio.CancelledError()])
async def test_the_slot_comes_back_when_the_run_crashes_or_is_cancelled(error: BaseException) -> None:
    slots = _Slots()
    with pytest.raises(type(error)):
        await _run(slots=slots, state={"local_first_model": _LOCAL}, runner_error=error)
    assert slots.released == ["lease-1"]


@pytest.mark.asyncio
async def test_a_cloud_run_never_touches_the_slots() -> None:
    slots = _Slots()
    model, *_ = await _run(slots=slots)
    assert model == BUILTIN_SEED["analysis"]["model"]
    assert slots.taken == [] and slots.released == []
