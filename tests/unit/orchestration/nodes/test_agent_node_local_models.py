"""Local models through run_agent_node: local-first while a slot is free, and the one-at-a-time slot."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from embry0.agents.executor import AgentOutput
from embry0.orchestration.nodes.agent import run_agent_node
from embry0.safety.error_codes import ErrorCode
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from tests.fixtures.budget_settings import BudgetStore
from tests.fixtures.provider_catalogue import enable_providers

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
    enable_providers(monkeypatch, "custom")


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
    configurable: dict[str, Any] | None = None,
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
                # The fake database holds no settings documents: the job's budget is the section's defaults.
                "settings_store": BudgetStore(),
                "traces_repo": traces,
                "job_id": "job-1",
                "llm_providers": providers,
                "local_slots": slots,
                **(configurable or {}),
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


# ---- #174: the slot is the server's the run reaches ----


def _server_names() -> Any:
    from embry0.services import local_slots

    names = [getattr(local_slots, name, None) for name in ("PROXY_SERVER", "UNKNOWN_SERVER")]
    assert None not in names, "services.local_slots names no server a run can be on"
    return names


class _Sandboxes:
    """The sandbox manager's record of the accounts it put in each container."""

    def __init__(self, held: dict[str, Any]) -> None:
        self.held = held

    def last_llm_credentials(self, container_id: str) -> dict[str, Any]:
        return dict(self.held) if container_id == "c1" else {}


class _Resolver:
    def __init__(self, credential_id: str | None = "cred-resolved", error: Exception | None = None) -> None:
        self.credential_id, self.error = credential_id, error
        self.asked: list[tuple[str, str, str]] = []

    async def resolve_llm(self, repo: str, provider: str, *, job_id: str = "") -> Any:
        self.asked.append((repo, provider, job_id))
        if self.error is not None:
            raise self.error
        from types import SimpleNamespace

        return SimpleNamespace(id=self.credential_id)


def _held(credential_id: str | None) -> dict[str, Any]:
    from types import SimpleNamespace

    return {"anthropic": SimpleNamespace(id="cred-claude"), "custom": SimpleNamespace(id=credential_id)}


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [{"local_first_model": _LOCAL}, {"agent_models_override": {"*": _LOCAL}}])
async def test_a_run_takes_a_slot_of_the_server_its_sandbox_s_credential_names(state: dict[str, Any]) -> None:
    slots, resolver = _Slots(), _Resolver()
    model, *_ = await _run(
        slots=slots,
        state=state,
        configurable={"sandbox_manager": _Sandboxes(_held("cred-box")), "credential_resolver": resolver},
    )
    assert model == _LOCAL
    assert [t.get("server") for t in slots.taken] == ["cred-box"], (
        "a local-first run's slot and a must-use run's are the server the sandbox's custom credential names"
    )
    assert resolver.asked == [], "the sandbox's own record is read first"


@pytest.mark.asyncio
async def test_without_the_sandbox_s_record_the_job_s_credential_names_the_server() -> None:
    slots, resolver = _Slots(), _Resolver()
    await _run(
        slots=slots,
        state={"agent_models_override": {"*": _LOCAL}, "repo": "acme/widgets"},
        configurable={"credential_resolver": resolver},
    )
    assert [t.get("server") for t in slots.taken] == ["cred-resolved"], (
        "a sandbox this process did not create (a resume after a restart) takes its slot on the job's credential"
    )
    assert resolver.asked == [("acme/widgets", "custom", "job-1")], "resolved as the job's run would be"


@pytest.mark.asyncio
async def test_every_run_through_the_environment_s_proxy_is_on_its_one_upstream() -> None:
    from embry0.config import Embry0Config

    PROXY_SERVER, _ = _server_names()
    slots = _Slots()
    await _run(
        slots=slots,
        state={"agent_models_override": {"*": _LOCAL}},
        configurable={
            "sandbox_manager": _Sandboxes(_held("cred-box")),
            "embry0_config": Embry0Config.model_construct(local_llm_upstream="http://models.test:8000/v1"),
        },
    )
    assert [t.get("server") for t in slots.taken] == [PROXY_SERVER], (
        "LOCAL_LLM_UPSTREAM sends every custom run to one root whatever its credential: one server, one cap"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("resolver", [None, _Resolver(error=RuntimeError("registry down"))])
async def test_a_server_that_cannot_be_told_shares_one_cap(resolver: _Resolver | None) -> None:
    _, UNKNOWN_SERVER = _server_names()
    slots = _Slots()
    extra = {"credential_resolver": resolver} if resolver is not None else {}
    model, *_ = await _run(slots=slots, state={"agent_models_override": {"*": _LOCAL}}, configurable=extra)
    assert model == _LOCAL and [t.get("server") for t in slots.taken] == [UNKNOWN_SERVER]


@pytest.mark.asyncio
async def test_a_credential_too_deep_to_read_fails_the_run_instead_of_sharing_a_cap() -> None:
    """#527: a stored value too deep to read is never taken for a default, here the shared cap of servers that
    cannot be told."""
    from embry0.storage.database import StoredValueTooDeep

    slots = _Slots()
    with pytest.raises(StoredValueTooDeep):
        await _run(
            slots=slots,
            state={"agent_models_override": {"*": _LOCAL}},
            configurable={"credential_resolver": _Resolver(error=StoredValueTooDeep("credentials"))},
        )
    assert slots.taken == [], "no slot was taken on a server read as unknown"
