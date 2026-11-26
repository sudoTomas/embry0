"""An agent run waits for a spent account only as its job's account_wait settings say (#173).

The executor puts the job's own ``account_wait`` values, read when the job
started, in every run's graph config. ``enabled: false``: a run whose account
is spent still moves to a ready model of the fallback order; with none it
fails with the account's own code, as before waits, and leaves no wait for a
pipeline that parks at a point of its own. ``max_wait_seconds`` cuts a wait's
end, and ``local_busy_retry_seconds`` is how long a forced local run with no
slot waits. The section is imported inside each test, so a tree without it
fails each one with its own message.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from embry0.agents.fallback import Pause
from embry0.services.account_waits import LOCAL_BUSY, AccountWaitInterrupt
from tests.fixtures.provider_catalogue import enable_providers
from tests.unit.orchestration.nodes.test_agent_node_spent_account import RESET, _Resolver, _run, _spent

pytestmark = pytest.mark.asyncio

QUARANTINED = {"anthropic": Pause("anthropic", "quota_exhausted", RESET)}


def _account_wait(**values: Any) -> Any:
    """The job's account_wait values: ``values`` over the section's defaults."""
    try:
        from embry0.settings.sections.account_wait import ACCOUNT_WAIT
    except ImportError:
        pytest.fail("the settings document has no account_wait section: whether a job waits is not a setting")
    return ACCOUNT_WAIT.validate(values)


def _no_local_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    import embry0.orchestration.nodes.agent as agent_mod

    class _Slots:
        async def acquire(self, **_kwargs: Any) -> None:
            return None

        async def release(self, _lease: str) -> None:
            return None

    async def _slots(_config: Any) -> tuple[_Slots, int]:
        return _Slots(), 1

    enable_providers(monkeypatch, "custom")
    monkeypatch.setattr(agent_mod, "_local_slots", _slots)
    monkeypatch.setattr(agent_mod, "_slot_wait_seconds", lambda _config: 0.0)


async def test_a_job_that_may_not_wait_fails_its_spent_run_with_the_account_s_code() -> None:
    off = _account_wait(enabled=False)
    try:
        updates, ran, _traces, health, _seen = await _run(order=[], results=[_spent("triage")], account_wait=off)
    except AccountWaitInterrupt:
        pytest.fail("a job whose account_wait.enabled is false parked instead of failing")
    assert ran == ["claude-sonnet-5"], f"the spent run is not run again: {ran}"
    entry = updates["agent_outputs"][-1]
    assert entry["is_error"] and entry["error_code"] == "ERR_PROVIDER_QUOTA_EXHAUSTED", (
        f"the run fails with the account's own code, as before waits: {entry.get('error_code')}"
    )
    assert "account_wait" not in entry, "no wait is left on the entry for a pipeline to park on"
    assert health.reports, "the spent account is still quarantined at once, so the next job's pre-check sees it"


async def test_a_job_that_may_not_wait_still_moves_to_a_ready_model() -> None:
    off = _account_wait(enabled=False)
    updates, ran, *_ = await _run(order=["grok-4.7"], results=[_spent("triage")], account_wait=off)
    assert ran == ["claude-sonnet-5", "grok-4.7"], f"the fallback order still takes the run: {ran}"
    assert not updates["agent_outputs"][-1]["is_error"]


@pytest.mark.parametrize("waits", [True, False], ids=["a pipeline that resumes", "one that parks at its own point"])
async def test_a_job_that_may_not_wait_does_not_start_a_run_on_an_account_known_spent(waits: bool) -> None:
    off = _account_wait(enabled=False)
    try:
        updates, ran, *_ = await _run(order=[], resolver=_Resolver(paused=QUARANTINED), waits=waits, account_wait=off)
    except AccountWaitInterrupt:
        pytest.fail("a job whose account_wait.enabled is false parked before its run started")
    assert ran == [], f"not started on an account known spent: {ran}"
    entry = updates["agent_outputs"][-1]
    assert entry["error_code"] == "ERR_PROVIDER_QUOTA_EXHAUSTED", entry.get("error_code")
    assert "account_wait" not in entry, "nothing to park on: the job fails"
    assert "account_wait.enabled is false" in str(entry.get("error_message")), (
        f"the failure says why the job did not wait: {entry.get('error_message')}"
    )


async def test_a_rate_limited_account_fails_a_job_that_may_not_wait_as_rate_limited() -> None:
    off = _account_wait(enabled=False)
    limited = _Resolver(paused={"anthropic": Pause("anthropic", "rate_limited", RESET)})
    updates, ran, *_ = await _run(order=[], resolver=limited, account_wait=off)
    assert ran == [] and updates["agent_outputs"][-1]["error_code"] == "ERR_PROVIDER_RATE_LIMITED"


async def test_a_forced_local_run_with_no_slot_fails_when_its_job_may_not_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_local_slot(monkeypatch)
    try:
        updates, ran, *_ = await _run(
            state={"agent_models_override": {"triage": "custom/qwen"}},
            providers=("anthropic", "custom"),
            account_wait=_account_wait(enabled=False),
        )
    except AccountWaitInterrupt:
        pytest.fail("a job whose account_wait.enabled is false waited for a local slot")
    entry = updates["agent_outputs"][-1]
    assert ran == [] and entry["error_code"] == "ERR_LOCAL_MODEL_BUSY", entry.get("error_code")
    assert "account_wait" not in entry, "nothing to park on: the job fails"


async def test_the_job_s_max_wait_cuts_its_run_s_wait() -> None:
    before = datetime.now(UTC)
    with pytest.raises(AccountWaitInterrupt) as stopped:
        await _run(order=[], results=[_spent("triage")], account_wait=_account_wait(max_wait_seconds=3600))
    until = stopped.value.wait.until
    assert until < RESET, "a reset further out than the job's max_wait_seconds is cut to it"
    assert before + timedelta(seconds=3600) <= until <= datetime.now(UTC) + timedelta(seconds=3600), until


async def test_the_job_s_local_busy_retry_is_how_long_a_forced_local_run_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_local_slot(monkeypatch)
    before = datetime.now(UTC)
    with pytest.raises(AccountWaitInterrupt) as stopped:
        await _run(
            state={"agent_models_override": {"triage": "custom/qwen"}},
            providers=("anthropic", "custom"),
            account_wait=_account_wait(local_busy_retry_seconds=1800),
        )
    wait = stopped.value.wait
    assert wait.reason == LOCAL_BUSY
    assert before + timedelta(seconds=1800) <= wait.until <= datetime.now(UTC) + timedelta(seconds=1800), (
        f"the job's local_busy_retry_seconds, not five minutes: {wait.until - before}"
    )


async def test_every_qa_app_s_run_reads_the_job_s_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """A qa app's sub-graph starts from a state of its own: the job's values reach its runs through the config."""
    from embry0.workflows.qa.orchestrator import qa_orchestrator_node
    from embry0.workflows.qa.subtask_result_schema import CacheHits, SubTaskResult, SubTaskStatus
    from tests.unit.workflows.test_spent_account_park_points import _qa_state, _two_apps

    off = _account_wait(enabled=False)
    _two_apps(monkeypatch)
    seen: list[Any] = []

    async def fake_run_subtask(resolved: Any, **kw: Any) -> SubTaskResult:
        seen.append(kw["config"]["configurable"].get("account_wait"))
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.INCONCLUSIVE,
            duration_ms=1,
            cache_hits=CacheHits(),
            error_code="ERR_PROVIDER_QUOTA_EXHAUSTED",
        )

    class _Results:
        async def upsert_with_boot_phase(self, **_kw: Any) -> None:
            return None

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)
    await qa_orchestrator_node(
        _qa_state(),
        {"configurable": {"account_waits": True, "account_wait": off, "qa_app_results_repo": _Results()}},
    )
    assert len(seen) == 2 and all(carried is off for carried in seen), (
        f"each app's run must read the job's account_wait values, not the defaults: {seen}"
    )
