"""Local models: the slots, the pre-check's local-first route, the watcher's stamp and the fallback order."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from embry0.agents.fallback import Pause, runnable_fallback_models
from embry0.services._executor_run import _resolve_local_first
from embry0.services.credentials.fallback import plan_model_fallbacks
from embry0.services.local_slots import LocalSlots
from tests.unit.services.test_provider_fallback_precheck import FakeResolver, _policy
from tests.unit.services.test_watcher import _config, _service

_LOCAL = "custom/qwen"


@pytest.fixture(autouse=True)
def _custom_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "custom,openrouter")


class _Leases:
    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.calls = 0
        self.released: list[str] = []

    async def try_take(self, **_kwargs: Any) -> str | None:
        self.calls += 1
        answer = self.answers.pop(0) if self.answers else None
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def release(self, lease_id: str) -> None:
        self.released.append(lease_id)
        if lease_id == "boom":
            raise RuntimeError("database down")


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def _slots(answers: list[Any]) -> tuple[LocalSlots, _Leases, _Clock]:
    leases, clock = _Leases(answers), _Clock()
    return LocalSlots(leases, sleep=clock.sleep, clock=clock, poll_seconds=5.0), leases, clock


_ARGS = {"cap": 1, "job_id": "job-1", "agent_type": "analysis", "model": _LOCAL, "run_seconds": 600}


async def test_try_acquire_never_waits_and_a_failed_read_is_no_slot() -> None:
    slots, leases, clock = _slots([None, RuntimeError("db"), "lease-1"])
    assert await slots.try_acquire(**_ARGS) is None
    assert await slots.try_acquire(**_ARGS) is None, "no lease, no run on the shared server"
    assert await slots.try_acquire(**_ARGS) == "lease-1"
    assert clock.now == 0.0


async def test_acquire_waits_for_a_slot_and_says_so_once() -> None:
    slots, leases, clock = _slots([None, None, None, "lease-1"])
    waits: list[float] = []
    lease = await slots.acquire(**_ARGS, wait_seconds=60, on_wait=lambda: waits.append(clock.now))
    assert lease == "lease-1"
    assert waits == [0.0] and clock.now == 15.0 and leases.calls == 4


async def test_acquire_gives_up_after_the_wait() -> None:
    slots, leases, clock = _slots([])
    assert await slots.acquire(**_ARGS, wait_seconds=12) is None
    assert clock.now == 12.0, "never waits past the limit"


async def test_release_never_raises() -> None:
    slots, leases, _ = _slots([])
    await slots.release("boom")
    await slots.release("lease-1")
    assert leases.released == ["boom", "lease-1"]


async def test_the_pre_check_adds_the_local_route_when_the_job_may_use_it() -> None:
    resolver = FakeResolver({})
    resolved: dict[str, Any] = {"anthropic": object()}
    assert await _resolve_local_first(resolver, "acme/app", _LOCAL, resolved, "job-1") == "custom"
    assert set(resolved) == {"anthropic", "custom"}, "its own models' keys stay, for when the slots are taken"


@pytest.mark.parametrize(
    ("resolver", "model"),
    [
        (FakeResolver({"custom": Pause("custom", "rate_limited")}), _LOCAL),
        (FakeResolver({}, missing={"custom"}), _LOCAL),
        (FakeResolver({}), "claude-sonnet-5"),
        (FakeResolver({}), ""),
        (FakeResolver({}), None),
    ],
    ids=["paused", "no-account", "not-local", "empty", "none"],
)
async def test_the_pre_check_drops_a_preference_it_cannot_serve(resolver: FakeResolver, model: Any) -> None:
    resolved: dict[str, Any] = {"anthropic": object()}
    assert await _resolve_local_first(resolver, "acme/app", model, resolved, "job-1") is None
    assert set(resolved) == {"anthropic"}


async def test_no_preference_while_the_custom_provider_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openrouter")
    resolved: dict[str, Any] = {}
    assert await _resolve_local_first(FakeResolver({}), "acme/app", _LOCAL, resolved, "job-1") is None


def test_a_local_model_counts_in_the_fallback_order() -> None:
    order = ["grok-4.7", _LOCAL, "custom/", "local/qwen"]
    assert runnable_fallback_models(order, []) == ["grok-4.7", _LOCAL], "a bare prefix or another prefix is not one"


async def test_the_local_model_stands_in_last() -> None:
    resolver = FakeResolver({"anthropic": Pause("anthropic", "quota_exhausted"), "xai": Pause("xai", "rate_limited")})
    plan = await plan_model_fallbacks(
        resolver, "acme/app", ("anthropic",), job_id="job-1", model_policy=_policy(["grok-4.7", _LOCAL])
    )
    assert (plan["anthropic"].model, plan["anthropic"].provider) == (_LOCAL, "custom")


async def test_the_watcher_stamps_its_local_first_model_on_each_analysis() -> None:
    svc = _service(config=_config(watcher_job_budget_usd=1.25), job_result={"status": "failed", "job_id": "job-w1"})
    svc._local_first_model = AsyncMock(return_value=_LOCAL)
    await svc._run_analysis_job("task text")
    assert svc._jobs.create.await_args.kwargs["pipeline_config"] == {
        "max_budget_usd": 1.25,
        "budget_overrun_mode": "hard",
        "local_first_model": _LOCAL,
    }


async def test_the_watcher_stamps_nothing_without_a_setting_or_on_a_failed_read() -> None:
    for read in (AsyncMock(return_value=""), AsyncMock(side_effect=RuntimeError("db down")), None):
        svc = _service(config=_config(watcher_job_budget_usd=1.25), job_result={"status": "failed", "job_id": "j"})
        svc._local_first_model = read
        await svc._run_analysis_job("task text")
        assert "local_first_model" not in svc._jobs.create.await_args.kwargs["pipeline_config"]


async def test_a_tick_run_with_a_chosen_model_is_not_stamped() -> None:
    from embry0.services.run_with import RunWith

    svc = _service(config=_config(watcher_job_budget_usd=1.25), job_result={"status": "failed", "job_id": "j"})
    svc._local_first_model = AsyncMock(return_value=_LOCAL)
    svc._run_with = RunWith(model="claude-opus-5")
    await svc._run_analysis_job("task text")
    config = svc._jobs.create.await_args.kwargs["pipeline_config"]
    assert "local_first_model" not in config and config["agent_models_override"]["*"] == "claude-opus-5"
