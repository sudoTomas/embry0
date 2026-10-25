"""Provider fallback at the account pre-check (spec 2026-10-25).

The pre-check plans one stand-in model per paused provider and resolves its
provider's key before the sandbox exists; an empty order changes nothing, and
a key a person chose for the job is never planned around.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.agents.fallback import Pause
from embry0.services.credentials.fallback import job_account_failures, plan_model_fallbacks
from embry0.services.credentials.resolver import CredentialMissingError, ResolvedCredential
from embry0.services.issue_executor import IssueExecutor

UNTIL = datetime(2026, 10, 26, tzinfo=UTC)
ORDER = ["grok-4.7", "openrouter/openai/gpt-6.1-sol"]


@pytest.fixture(autouse=True)
def _openrouter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "openrouter")


class FakeResolver:
    """``llm_pause`` from a table; records what was asked and what was resolved."""

    def __init__(self, paused: dict[str, Pause], *, pinned: set[str] = frozenset(), missing: set[str] = frozenset()):
        self.paused = paused
        self.pinned = set(pinned)
        self.missing = set(missing)
        self.asked: list[str] = []
        self.resolved: list[tuple[str, ...]] = []

    async def job_pinned(self, job_id: str, provider: str) -> bool:
        return provider in self.pinned

    async def llm_pause(self, repo: str, provider: str) -> Pause | None:
        self.asked.append(provider)
        return self.paused.get(provider)

    async def resolve_llm_for_job(self, repo, providers, *, job_id, parent_job_id=None):
        providers = tuple(providers)
        self.resolved.append(providers)
        if any(p in self.missing for p in providers):
            raise CredentialMissingError(f"no {providers} credential")
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


def _policy(order: list[str] | None = None, disabled: list[str] | None = None) -> dict[str, Any]:
    return {
        "default_model": "",
        "disabled_models": disabled or [],
        "tier_models": {"model_heavy": "claude-opus-5", "model_medium": "claude-sonnet-5", "model_light": ""},
        "fallback_models": list(order or []),
    }


async def test_the_next_ready_provider_is_picked() -> None:
    resolver = FakeResolver(
        {
            "anthropic": Pause("anthropic", "quota_exhausted", UNTIL),
            "xai": Pause("xai", "rate_limited"),
        }
    )
    plan = await plan_model_fallbacks(resolver, "acme/app", ("anthropic",), job_id="job-1", model_policy=_policy(ORDER))
    assert plan["anthropic"].model == "openrouter/openai/gpt-6.1-sol", "xai is paused too: the next one"
    assert (plan["anthropic"].reason, plan["anthropic"].until) == ("quota_exhausted", UNTIL.isoformat())


async def test_an_empty_order_plans_nothing_and_asks_nothing() -> None:
    resolver = FakeResolver({"anthropic": Pause("anthropic", "quota_exhausted", UNTIL)})
    for policy in (None, _policy([]), _policy(["grok-4.7"], disabled=["grok-4.7"])):
        assert await plan_model_fallbacks(resolver, "acme/app", ("anthropic",), job_id="j", model_policy=policy) == {}
    assert resolver.asked == [], "off means no lookups at all"


async def test_a_pinned_key_is_never_planned_around() -> None:
    resolver = FakeResolver({"anthropic": Pause("anthropic", "quota_exhausted", UNTIL)}, pinned={"anthropic"})
    plan = await plan_model_fallbacks(resolver, "acme/app", ("anthropic",), job_id="j", model_policy=_policy(ORDER))
    assert plan == {}
    failures = {"anthropic": Pause("anthropic", "quota_exhausted")}
    plan = await plan_model_fallbacks(
        resolver, "acme/app", ("anthropic",), job_id="j", model_policy=_policy(ORDER), job_failures=failures
    )
    assert plan == {}, "nor after the job's own failure on it"


async def test_a_stand_in_needs_a_provider_that_is_ready_and_not_pinned_away() -> None:
    resolver = FakeResolver({"anthropic": Pause("anthropic", "missing")})
    plan = await plan_model_fallbacks(
        resolver, "acme/app", ("anthropic",), job_id="j", model_policy=_policy(["claude-haiku-4-5", "grok-4.7"])
    )
    assert plan["anthropic"].model == "grok-4.7", "a model of the paused provider itself is skipped"


async def test_the_job_s_own_account_failure_pauses_within_its_window() -> None:
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    config = SimpleNamespace(credential_rate_limit_quarantine_seconds=900, credential_quota_quarantine_seconds=3600)
    rows = [  # newest first, as the traces repository lists them
        {"model": "grok-4.7", "error_code": None, "created_at": now - timedelta(minutes=1)},
        {
            "model": "claude-opus-5",
            "error_code": "ERR_PROVIDER_QUOTA_EXHAUSTED",
            "created_at": now - timedelta(minutes=5),
        },
        {"model": "grok-4.7", "error_code": "ERR_PROVIDER_RATE_LIMITED", "created_at": now - timedelta(minutes=2)},
        {"model": "claude-sonnet-5", "error_code": None, "created_at": now - timedelta(minutes=9)},
    ]
    paused = job_account_failures(rows, config, now)
    assert set(paused) == {"anthropic"}, "xai's latest run succeeded; anthropic's latest ran out"
    assert paused["anthropic"].until == now + timedelta(minutes=55)
    stale = job_account_failures(rows[1:2], config, now + timedelta(hours=2))
    assert stale == {}, "past the window the account is the registry's to judge again"


# --- the pre-check itself ------------------------------------------------------


class _ProviderRowDb:
    def __init__(self, order: list[str]):
        self.order = order

    async def fetchrow(self, sql: str, *_args):
        return {
            "model_heavy": "claude-opus-5",
            "model_medium": "claude-sonnet-5",
            "model_light": "claude-haiku-4-5",
            "default_model": "",
            "disabled_models": [],
            "fallback_models": self.order,
        }


def _executor(resolver: FakeResolver, order: list[str]) -> IssueExecutor:
    executor = IssueExecutor.__new__(IssueExecutor)
    executor._credential_resolver = resolver
    executor._db = _ProviderRowDb(order)
    executor._jobs = MagicMock()
    executor._jobs.get = AsyncMock(return_value=None)
    executor._traces = MagicMock()
    executor._traces.list_all = AsyncMock(return_value=([], 0))
    executor._config = SimpleNamespace(
        credential_rate_limit_quarantine_seconds=900, credential_quota_quarantine_seconds=3600
    )
    return executor


@pytest.fixture
def precheck(monkeypatch: pytest.MonkeyPatch):
    """The pre-check of a job whose agent definitions are Claude-only."""
    from embry0.services import _executor_run

    async def claude_only(_self):
        return [{"model": "claude-opus-5"}, {"model": "claude-sonnet-5"}]

    monkeypatch.setattr(_executor_run, "_agent_definitions_for_precheck", claude_only)

    async def run(executor: IssueExecutor):
        return await _executor_run._precheck_llm_accounts(executor, "job-1", {"repo": "acme/app", "job_id": "job-1"})

    return run


async def test_the_precheck_resolves_the_stand_in_s_key_and_hands_the_plan_on(precheck) -> None:
    resolver = FakeResolver({"anthropic": Pause("anthropic", "quota_exhausted", UNTIL)}, missing={"anthropic"})
    state, providers, kinds, ids, fallbacks = await precheck(_executor(resolver, ORDER))
    assert fallbacks == {
        "anthropic": {"model": "grok-4.7", "provider": "xai", "reason": "quota_exhausted", "until": UNTIL.isoformat()}
    }
    assert providers == ("xai",), "anthropic had no account left, so only the stand-in's key goes in"
    assert ids == {"xai": "cred-xai"} and state["llm_credential_ids"] == ids
    assert resolver.resolved[0] == ("xai",)


async def test_a_paused_provider_that_still_resolves_stays_beside_the_stand_in(precheck) -> None:
    resolver = FakeResolver({"anthropic": Pause("anthropic", "quota_exhausted", UNTIL)})
    _state, providers, _kinds, _ids, fallbacks = await precheck(_executor(resolver, ORDER))
    assert providers == ("anthropic", "xai")
    assert set(fallbacks) == {"anthropic"}


async def test_an_empty_order_is_today_s_precheck(precheck) -> None:
    resolver = FakeResolver({"anthropic": Pause("anthropic", "quota_exhausted", UNTIL)})
    executor = _executor(resolver, [])
    _state, providers, _kinds, _ids, fallbacks = await precheck(executor)
    assert (providers, fallbacks) == (("anthropic",), {})
    assert resolver.asked == [] and resolver.resolved == [("anthropic",)]
    executor._traces.list_all.assert_not_awaited()


async def test_the_plan_reaches_the_graph_config(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver = FakeResolver({"anthropic": Pause("anthropic", "quota_exhausted", UNTIL)})
    executor = _executor(resolver, ORDER)
    seen: dict[str, Any] = {}

    def build_graph_config(job_id, **kwargs):
        seen.update(kwargs)
        return {"configurable": {"thread_id": job_id}}

    class _Saver:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    executor._build_graph_config = build_graph_config  # type: ignore[method-assign]
    executor._select_workflow = lambda _pipeline: MagicMock()  # type: ignore[method-assign]
    executor._process_stream = AsyncMock(return_value=(None, False, None))  # type: ignore[method-assign]
    executor._database_url = ""
    monkeypatch.setattr("embry0.services._executor_run.checkpointer_context", lambda _url: _Saver())

    async def no_definitions(_self):
        return [{"model": "claude-opus-5"}]

    monkeypatch.setattr("embry0.services._executor_run._agent_definitions_for_precheck", no_definitions)
    await executor._execute_workflow_stream({"repo": "acme/app", "job_id": "job-1"}, "iss-1", "job-1")
    assert seen["model_fallbacks"]["anthropic"]["model"] == "grok-4.7"
    assert seen["llm_providers"] == ("anthropic", "xai")


def test_the_graph_config_carries_the_plan() -> None:
    executor = IssueExecutor(
        issues_repo=MagicMock(),
        jobs_repo=MagicMock(),
        traces_repo=MagicMock(),
        workflow_registry=MagicMock(),
        database_url="postgresql://test/test",
    )
    plan = {"anthropic": {"model": "grok-4.7", "provider": "xai", "reason": "missing", "until": None}}
    assert executor._build_graph_config("job-1", model_fallbacks=plan)["configurable"]["model_fallbacks"] == plan
    assert executor._build_graph_config("job-1")["configurable"]["model_fallbacks"] == {}, (
        "no plan: every agent runs its own model"
    )
