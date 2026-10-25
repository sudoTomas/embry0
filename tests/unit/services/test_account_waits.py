"""Spent account waits (spec 2026-10-25): what a wait is, when it ends, and the loop that goes on."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from embry0.agents.fallback import Pause
from embry0.services.account_waits import (
    LOCAL_BUSY,
    WAIT_MAX,
    AccountWait,
    AccountWaitLoop,
    local_busy_wait,
    main_provider,
    provider_pause,
    wait_for,
)

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
CONFIG = SimpleNamespace(credential_rate_limit_quarantine_seconds=900, credential_quota_quarantine_seconds=3600)


def test_a_wait_round_trips_through_its_record() -> None:
    wait = AccountWait("anthropic", "quota_exhausted", NOW, model="claude-opus-5", agent="developer", watch=True)
    record = wait.with_mode("relaunch").with_resume({"choice": "continue_retrying"}).record()
    assert record["until"] == "2026-10-25T12:00:00+00:00", "UTC ISO for the API"
    back = AccountWait.from_record(record)
    assert back is not None
    assert (back.provider, back.mode, back.resume_with, back.watch) == (
        "anthropic",
        "relaunch",
        {"choice": "continue_retrying"},
        True,
    )
    assert AccountWait.from_record({"provider": "anthropic"}) is None, "no end, no wait"
    assert AccountWait.from_record("nonsense") is None


def test_an_answer_that_is_not_json_is_not_kept() -> None:
    wait = AccountWait("anthropic", "quota_exhausted", NOW).with_resume(object())
    assert wait.resume_with is None


def test_a_pause_with_an_end_is_a_wait_until_then() -> None:
    until = NOW + timedelta(hours=12)
    wait = wait_for(Pause("anthropic", "quota_exhausted", until), CONFIG, NOW, model="m", agent="triage")
    assert wait is not None and wait.until == until and wait.agent == "triage"


def test_a_spent_or_rate_limited_pause_with_no_end_waits_its_quarantine_window() -> None:
    rate = wait_for(Pause("xai", "rate_limited"), CONFIG, NOW)
    quota = wait_for(Pause("xai", "quota_exhausted"), CONFIG, NOW)
    assert rate is not None and rate.until == NOW + timedelta(seconds=900)
    assert quota is not None and quota.until == NOW + timedelta(seconds=3600)


@pytest.mark.parametrize("reason", ["missing", "not_enabled", "auth_rejected", "disabled:operator"])
def test_a_pause_that_does_not_end_by_itself_is_no_wait(reason: str) -> None:
    assert wait_for(Pause("anthropic", reason), CONFIG, NOW) is None, "the job fails as before"


def test_a_pause_already_over_is_no_wait_and_a_far_one_is_capped() -> None:
    assert wait_for(Pause("anthropic", "quota_exhausted", NOW - timedelta(seconds=1)), CONFIG, NOW) is None
    far = wait_for(Pause("anthropic", "quota_exhausted", NOW + timedelta(days=30)), CONFIG, NOW)
    assert far is not None and far.until == NOW + WAIT_MAX
    assert wait_for(None, CONFIG, NOW) is None


def test_the_local_model_busy_waits_a_few_minutes() -> None:
    wait = local_busy_wait("custom/qwen", "analysis", NOW)
    assert (wait.provider, wait.reason) == ("custom", LOCAL_BUSY)
    assert NOW < wait.until <= NOW + timedelta(minutes=10)
    assert "free slot" in wait.describe()


def test_the_words_name_who_waits_and_why_but_no_time() -> None:
    wait = AccountWait("anthropic", "quota_exhausted", NOW, model="claude-opus-5", agent="developer")
    text = wait.describe()
    assert "developer on claude-opus-5 waits for the anthropic account (quota exhausted)" in text
    assert "2026" not in text, "the page shows the reset in the viewer's own time"
    chosen = AccountWait("anthropic", "quota_exhausted", NOW, chosen=True).describe()
    assert "a person chose this model or key" in chosen


def test_the_main_provider_is_the_default_models_else_the_most_named() -> None:
    claude = [{"model": "claude-sonnet-5"}, {"model": "claude-opus-5"}, {"model": "grok-4.7"}]
    assert main_provider(claude, None) == "anthropic"
    assert main_provider(claude, {"default_model": "grok-4.7"}) == "xai"
    grok = [{"model": "grok-4.7"}, {"model": "grok-4.5"}, {"model": "claude-opus-5"}]
    assert main_provider(grok, {"default_model": ""}) == "xai"
    assert main_provider([], None) == "anthropic"
    assert main_provider([{"model": "grok-4.7"}, {"model": "claude-opus-5"}], None) == "anthropic", "a tie"


class _Resolver:
    def __init__(
        self,
        *,
        registry: Pause | None = None,
        pinned: tuple[bool, Pause | None] = (False, None),
        uses_registry: bool = False,
    ) -> None:
        self.registry = registry
        self.pinned = pinned
        self.uses_registry = uses_registry

    async def llm_pause(self, repo: str, provider: str) -> Pause | None:
        return self.registry

    async def pinned_pause(self, job_id: str, provider: str) -> tuple[bool, Pause | None]:
        return self.pinned

    async def job_uses_registry(self, job_id: str, provider: str) -> bool:
        return self.uses_registry


@pytest.mark.asyncio
async def test_a_chosen_key_answers_for_itself() -> None:
    spent = Pause("anthropic", "quota_exhausted", NOW)
    pause, watch, pinned = await provider_pause(
        _Resolver(pinned=(True, spent), registry=None), "acme/app", "anthropic", job_id="job-1"
    )
    assert (pause, watch, pinned) == (spent, True, True)
    ready, _, pinned = await provider_pause(
        _Resolver(pinned=(True, None), registry=spent), "acme/app", "anthropic", job_id="job-1"
    )
    assert ready is None and pinned, "the person's key is usable: the pool's state does not matter"


@pytest.mark.asyncio
async def test_the_registry_wins_and_the_jobs_own_failure_counts_only_off_the_registry() -> None:
    quarantined = Pause("anthropic", "quota_exhausted", NOW)
    failed = {"anthropic": Pause("anthropic", "quota_exhausted", NOW + timedelta(hours=1))}
    pause, watch, _ = await provider_pause(_Resolver(registry=quarantined), "r", "anthropic", job_failures=failed)
    assert pause == quarantined and watch
    env_only, watch, _ = await provider_pause(
        _Resolver(uses_registry=False), "r", "anthropic", job_id="job-1", job_failures=failed
    )
    assert env_only == failed["anthropic"] and not watch, "an environment account: only the job knows"
    cleared, _, _ = await provider_pause(
        _Resolver(uses_registry=True), "r", "anthropic", job_id="job-1", job_failures=failed
    )
    assert cleared is None, "a registry account the registry calls usable is usable: a cleared quarantine counts"


class _Jobs:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.claimed: list[str] = []

    async def account_waiting(self) -> list[dict[str, Any]]:
        return self.rows

    async def claim_account_wait(self, job_id: str) -> bool:
        if job_id in self.claimed:
            return False
        self.claimed.append(job_id)
        return True


class _Executor:
    def __init__(self, *, draining: bool = False) -> None:
        self.draining = draining
        self.went_on: list[dict[str, Any]] = []

    def go_on_after_account_wait(self, job: dict[str, Any]) -> None:
        self.went_on.append(job)


def _row(job_id: str, until: datetime, *, watch: bool = False, reason: str = "quota_exhausted") -> dict[str, Any]:
    return {
        "job_id": job_id,
        "repo": "acme/app",
        "account_wait": AccountWait("anthropic", reason, until, watch=watch).record(),
    }


@pytest.mark.asyncio
async def test_the_loop_goes_on_when_the_time_has_come() -> None:
    jobs = _Jobs([_row("job-due", NOW - timedelta(seconds=1)), _row("job-later", NOW + timedelta(hours=1))])
    executor = _Executor()
    loop = AccountWaitLoop(jobs=jobs, executor=executor, clock=lambda: NOW)
    assert await loop.tick() == ["job-due"]
    assert [j["job_id"] for j in executor.went_on] == ["job-due"]
    assert executor.went_on[0]["account_wait"]["provider"] == "anthropic", "the wait rides along"


@pytest.mark.asyncio
async def test_the_loop_goes_on_early_once_the_registry_says_ready() -> None:
    later = NOW + timedelta(hours=10)
    rows = [_row("job-watched", later, watch=True), _row("job-env", later, watch=False)]
    jobs, executor = _Jobs(rows), _Executor()
    loop = AccountWaitLoop(jobs=jobs, executor=executor, resolver=_Resolver(registry=None), clock=lambda: NOW)
    assert await loop.tick() == ["job-watched"], "an environment account's wait ends at its time"
    still = AccountWaitLoop(
        jobs=_Jobs([_row("job-watched", later, watch=True)]),
        executor=_Executor(),
        resolver=_Resolver(registry=Pause("anthropic", "quota_exhausted", later)),
        clock=lambda: NOW,
    )
    assert await still.tick() == []


@pytest.mark.asyncio
async def test_the_loop_takes_a_job_once_and_not_while_draining() -> None:
    rows = [_row("job-1", NOW - timedelta(seconds=1))]
    jobs = _Jobs(rows)
    jobs.claimed.append("job-1")
    executor = _Executor()
    assert await AccountWaitLoop(jobs=jobs, executor=executor, clock=lambda: NOW).tick() == []
    assert executor.went_on == [], "someone else claimed it"
    draining = _Executor(draining=True)
    assert await AccountWaitLoop(jobs=_Jobs(rows), executor=draining, clock=lambda: NOW).tick() == []
