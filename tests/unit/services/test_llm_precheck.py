"""LLM accounts are resolved before a sandbox exists.

A provider named by an agent definition, the job override, or the model
policy is resolved inside the graph runner before ``_build_graph_config``.
State receives credential ids only. A provider that triage names for the
first time cannot be injected after the container exists, so that
executor error becomes ``ERR_CREDENTIAL_MISSING``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.safety.error_codes import ErrorCode, classify_failure
from embry0.services.credentials.resolver import CredentialMissingError, ResolvedCredential
from embry0.services.issue_executor import IssueExecutor

_TRIAGE_FIRST_PROVIDER = (
    "model 'grok-4.5' requires the xai-proxy (bearer file + EMBRY0_XAI_PROXY_URL) "
    "or XAI_API_KEY in the orchestrator environment (injected into the sandbox at create)"
)


def test_provider_first_requested_by_triage_fails_with_credential_missing() -> None:
    """Claude-only definitions, then triage picks Grok. The container already
    exists, so the executor's requires-key error is the job's code."""
    code = classify_failure(agent_error=_TRIAGE_FIRST_PROVIDER, stage="triage")
    assert code == ErrorCode.CREDENTIAL_MISSING, (
        f"a provider first requested by triage must be ERR_CREDENTIAL_MISSING, got {code}"
    )
    assert "xai" in _TRIAGE_FIRST_PROVIDER, "the executor message must name the provider"


def test_default_model_pulls_its_provider_into_the_sandbox() -> None:
    """#465: with Claude-only definitions and default_model=grok-4.7 every agent
    resolves to grok, so xai must be resolved before the container exists —
    the sandbox env cannot be changed afterwards."""
    from embry0.services.credentials.providers import providers_for_job

    claude_only = [{"model": "claude-opus-5"}, {"model": "claude-sonnet-5"}]
    tiers = {"model_heavy": "claude-opus-5", "model_medium": "claude-sonnet-5", "model_light": "claude-haiku-4-5"}

    without = providers_for_job(claude_only, None, {"default_model": "", "disabled_models": [], "tier_models": tiers})
    assert without == ("anthropic",), f"no default, Claude everywhere: only anthropic is needed, got {without}"

    with_default = providers_for_job(
        claude_only, None, {"default_model": "grok-4.7", "disabled_models": [], "tier_models": tiers}
    )
    assert with_default == ("anthropic", "xai"), (
        f"default_model=grok-4.7 must add xai to the job's providers, got {with_default}"
    )


@pytest.mark.asyncio
async def test_precheck_policy_carries_the_default_model() -> None:
    """The pre-check reads the same policy shape the agent node resolves with."""
    from embry0.services._executor_run import _model_policy_for_precheck

    class _ProviderRowDb:
        async def fetchrow(self, sql: str, *_args):
            assert "provider_config" in sql
            return {
                "model_heavy": "claude-opus-5",
                "model_medium": "claude-sonnet-5",
                "model_light": "claude-haiku-4-5",
                "default_model": "grok-4.7",
                "disabled_models": [],
            }

    executor = IssueExecutor.__new__(IssueExecutor)
    executor._db = _ProviderRowDb()

    policy = await _model_policy_for_precheck(executor)

    assert policy is not None
    assert policy.get("default_model") == "grok-4.7", f"the pre-check policy lost the default model: {policy}"


def _resolved(provider: str) -> ResolvedCredential:
    return ResolvedCredential(
        id=f"cred-{provider}",
        kind="llm_api_key" if provider == "xai" else "llm_oauth",
        provider=provider,
        host="api.x.ai" if provider == "xai" else "api.anthropic.com",
        label=provider,
        secret="super-secret-token",
        git_author_name=None,
        git_author_email=None,
        source="registry",
        trace=(),
    )


class _Saver:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _executor(resolver: MagicMock, *, build_graph_config) -> IssueExecutor:
    executor = IssueExecutor.__new__(IssueExecutor)
    executor._credential_resolver = resolver
    executor._db = None
    executor._jobs = MagicMock()
    executor._jobs.get = AsyncMock(return_value=None)
    executor._config = SimpleNamespace(default_auth_mode="oauth", default_execution_mode="sdk")
    executor._build_graph_config = build_graph_config  # type: ignore[method-assign]
    executor._select_workflow = lambda _pipeline: MagicMock()  # type: ignore[method-assign]
    executor._process_stream = AsyncMock(return_value=(None, False, None))  # type: ignore[method-assign]
    executor._database_url = ""
    return executor


@pytest.mark.asyncio
async def test_precheck_resolves_declared_providers_before_the_graph(monkeypatch) -> None:
    """A grok job override is resolved before graph config. State holds ids, never the secret."""
    order: list[str] = []
    seen: dict[str, object] = {}

    async def resolve_llm_for_job(repo, providers, *, job_id, parent_job_id=None):
        order.append("resolve")
        seen["repo"] = repo
        seen["providers"] = tuple(providers)
        seen["job_id"] = job_id
        return {provider: _resolved(provider) for provider in providers}

    resolver = MagicMock()
    resolver.resolve_llm_for_job = resolve_llm_for_job

    def build_graph_config(job_id, **kwargs):
        order.append("graph_config")
        seen["config_kwargs"] = kwargs
        return {"configurable": {"thread_id": job_id}}

    executor = _executor(resolver, build_graph_config=build_graph_config)

    monkeypatch.setattr(
        "embry0.services._executor_run.checkpointer_context",
        lambda _url: _Saver(),
    )

    state = {
        "repo": "acme/widgets",
        "job_id": "job-1",
        "agent_models_override": {"developer": "grok-4.5"},
    }
    await executor._execute_workflow_stream(state, "iss-1", "job-1")

    assert order == ["resolve", "graph_config"], f"resolve_llm_for_job must run before _build_graph_config, saw {order}"
    providers = seen["providers"]
    assert isinstance(providers, tuple)
    assert "anthropic" in providers, "anthropic is always in the provider set"
    assert "xai" in providers, "a grok job override must add xai before the sandbox exists"
    passed = executor._process_stream.await_args.args[1]
    ids = passed.get("llm_credential_ids")
    assert ids == {"anthropic": "cred-anthropic", "xai": "cred-xai"}, (
        f"state must carry credential ids only, got {ids!r}"
    )
    assert "super-secret-token" not in str(passed), "the resolved secret must not enter LangGraph state"
    assert "super-secret-token" not in str(seen["config_kwargs"]), "the resolved secret must not enter graph config"


@pytest.mark.asyncio
async def test_missing_llm_account_fails_before_the_graph(monkeypatch) -> None:
    resolver = MagicMock()
    resolver.resolve_llm_for_job = AsyncMock(side_effect=CredentialMissingError("no xai credential for 'acme/widgets'"))
    called: list[str] = []

    def build_graph_config(job_id, **kwargs):
        called.append(job_id)
        return {"configurable": {}}

    executor = _executor(resolver, build_graph_config=build_graph_config)
    monkeypatch.setattr(
        "embry0.services._executor_run.checkpointer_context",
        lambda _url: _Saver(),
    )
    try:
        await executor._execute_workflow_stream(
            {
                "repo": "acme/widgets",
                "agent_models_override": {"developer": "grok-4.5"},
            },
            "iss-1",
            "job-1",
        )
    except CredentialMissingError as exc:
        assert not called, "a missing LLM account must fail before _build_graph_config"
        assert "xai" in str(exc), "the failure must name the provider"
    else:
        raise AssertionError(
            "a provider with no account must fail with CredentialMissingError before _build_graph_config"
        )


@pytest.mark.asyncio
async def test_precheck_registers_the_resolved_secrets_with_the_scrubber(monkeypatch) -> None:
    """spec 3.6: an env-fallback LLM credential is not in the registry, so the
    job's resolution is what registers it."""
    from embry0.safety.redaction import known_secret_values, register_known_secrets

    register_known_secrets([], reset=True)
    try:

        async def resolve_llm_for_job(repo, providers, *, job_id, parent_job_id=None):
            return {provider: _resolved(provider) for provider in providers}

        resolver = MagicMock()
        resolver.resolve_llm_for_job = resolve_llm_for_job
        executor = _executor(resolver, build_graph_config=lambda job_id, **kw: {"configurable": {"thread_id": job_id}})
        monkeypatch.setattr("embry0.services._executor_run.checkpointer_context", lambda _url: _Saver())
        await executor._execute_workflow_stream({"repo": "acme/widgets", "job_id": "job-1"}, "iss-1", "job-1")
        assert "super-secret-token" in known_secret_values(), "every resolved LLM secret is a known scrub value"
    finally:
        register_known_secrets([], reset=True)
