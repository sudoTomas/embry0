"""SandboxManager and the llm-proxy for the hosted chat-completions providers (ADR 0033): no provider's key enters a
sandbox; the sandbox is enrolled at the proxy with each provider's server, its root's host resolved here, checked, and
pinned, at create and again at resume. A sandbox from before, holding a provider's key in its env, is not resumed."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from embry0.execution.proxy import llm_proxy
from embry0.execution.sandbox_manager import SandboxInitError, SandboxManager, SandboxReenrollError
from embry0.safety.error_codes import ErrorCode
from embry0.safety.redaction import known_secret_values, unregister_known_secrets
from embry0.sandbox.local_llm_token import LOCAL_LLM_PROXY_TOKEN_REL
from tests.fixtures.model_servers import fake_dns
from tests.unit.execution import test_sandbox_reenroll as reenroll
from tests.unit.execution.test_sandbox_manager import (  # noqa: F401
    _stub_egress_unless_overridden,
    _stub_isolation_unless_overridden,
)
from tests.unit.execution.test_sandbox_manager_llm import _env, _manager
from tests.unit.execution.test_sandbox_manager_openai_compatible import _CUSTOM, _key

_PROXY = "http://llm-proxy:9108"
#: Each hosted provider's API root (the shipped catalogue's) and the address its host answers with here.
_ROOTS = {
    "openai": ("https://api.openai.com/v1", "api.openai.com", "162.159.140.245"),
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter.ai", "104.18.2.115"),
    "google": (
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "generativelanguage.googleapis.com",
        "142.250.1.95",
    ),
    "groq": ("https://api.groq.com/openai/v1", "api.groq.com", "104.18.40.249"),
    "deepseek": ("https://api.deepseek.com", "api.deepseek.com", "116.205.40.120"),
}


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Each provider's host answers with its address; the custom server's is an address already."""
    return fake_dns(monkeypatch, {host: [address] for _root, host, address in _ROOTS.values()})


@pytest.fixture(autouse=True)
def _forget_test_keys() -> Any:
    yield
    unregister_known_secrets([*(f"{p}-secret-key-value" for p in _ROOTS), _CUSTOM.secret])


def _proxied_manager(resolved: Any) -> tuple[SandboxManager, Any, Any]:
    mgr, resolver, pm = _manager(resolved)
    pm.llm_proxy_url = _PROXY
    mgr._runtime.attach_policy = AsyncMock()  # type: ignore[method-assign]
    return mgr, resolver, pm


def _server(provider: str) -> llm_proxy.Upstream:
    root, _host, address = _ROOTS[provider]
    return llm_proxy.Upstream(
        credential_id=f"cred-{provider}", root=root, api_key=f"{provider}-secret-key-value", address=address
    )


def _bearer_writes(mgr: SandboxManager) -> int:
    return sum(
        1 for call in mgr._docker.build_exec_cmd.call_args_list if LOCAL_LLM_PROXY_TOKEN_REL in " ".join(call.args[1])
    )


# ---- create ----


@pytest.mark.parametrize("provider", sorted(_ROOTS))
async def test_a_hosted_provider_s_key_goes_to_the_proxy_and_never_into_the_sandbox(provider: str, dns) -> None:
    mgr, _, pm = _proxied_manager({provider: _key(provider, f"{provider}-secret-key-value")})
    await mgr.create("job-1", repo="acme/widgets", llm_providers=[provider])

    env = _env(mgr)
    assert not any(f"{provider}-secret-key-value" in value for value in env.values()), (
        f"the {provider} key is in the sandbox's env: {sorted(env)}"
    )
    assert not [name for name in env if name.startswith("EMBRY0_LLM_") and name.endswith("_API_KEY")], sorted(env)
    kwargs = pm.enroll_sandbox.await_args.kwargs
    assert kwargs.get("provider_servers") == {provider: _server(provider)}, (
        f"the sandbox is not enrolled with the provider's server, its root pinned to the checked address: {kwargs}"
    )
    assert isinstance(kwargs.get("limits"), llm_proxy.Limits), "the provider's server has no limits"
    assert "local_llm" not in kwargs, "a job on no custom provider was given a custom server"
    assert _bearer_writes(mgr) == 1, "the run has no bearer to reach the proxy with"
    assert f"{provider}-secret-key-value" in known_secret_values(), "the key is not scrubbed by its value"


async def test_custom_and_hosted_providers_are_enrolled_together(dns) -> None:
    mgr, _, pm = _proxied_manager({"custom": _CUSTOM, "groq": _key("groq", "groq-secret-key-value")})
    await mgr.create("job-1", repo="acme/widgets", llm_providers=["custom", "groq"])
    kwargs = pm.enroll_sandbox.await_args.kwargs
    assert set(kwargs.get("provider_servers") or {}) == {"groq"}, kwargs
    assert kwargs.get("local_llm") is not None, "the custom server went missing beside a hosted one"
    assert _bearer_writes(mgr) == 1
    assert {_CUSTOM.secret, "groq-secret-key-value"} <= set(known_secret_values()), (
        "a key the proxy is given is not scrubbed by its value"
    )


async def test_a_hosted_provider_s_job_does_not_start_without_the_proxy(dns) -> None:
    mgr, _, pm = _proxied_manager({"openai": _key("openai", "openai-secret-key-value")})
    pm.llm_proxy_url = ""
    pm.llm_proxy_held = "llm-proxy is not started: ... (docker rm -f local-llm-proxy)"
    with pytest.raises(SandboxInitError) as refused:
        await mgr.create("job-1", repo="acme/widgets", llm_providers=["openai"])
    assert refused.value.error_code == ErrorCode.SANDBOX_INIT
    assert "openai" in str(refused.value) and "docker rm -f local-llm-proxy" in str(refused.value), refused.value
    assert not mgr._docker.build_run_cmd.called, "a container was started for a job with no route to its provider"
    pm.enroll_sandbox.assert_not_awaited()


@pytest.mark.parametrize(
    "answers",
    [{}, {"api.openai.com": ["127.0.0.1"]}, {"api.openai.com": ["169.254.169.254"]}],
    ids=["unresolved", "loopback", "link-local"],
)
async def test_a_provider_root_that_is_not_reached_from_here_does_not_start(monkeypatch, answers) -> None:
    fake_dns(monkeypatch, answers)
    mgr, _, pm = _proxied_manager({"openai": _key("openai", "openai-secret-key-value")})
    with pytest.raises(SandboxInitError) as refused:
        await mgr.create("job-1", repo="acme/widgets", llm_providers=["openai"])
    assert "openai" in str(refused.value), refused.value
    assert not mgr._docker.build_run_cmd.called
    pm.enroll_sandbox.assert_not_awaited()


async def test_a_job_on_no_chat_provider_is_not_enrolled_with_the_proxy(dns) -> None:
    mgr, _, pm = _proxied_manager({"anthropic": _key("anthropic", "anthropic-secret-key-value")})
    await mgr.create("job-1", repo="acme/widgets", llm_providers=["anthropic"])
    kwargs = pm.enroll_sandbox.await_args.kwargs
    assert not {"provider_servers", "local_llm", "limits"} & set(kwargs), kwargs
    assert _bearer_writes(mgr) == 0


# ---- resume ----


def _reenrolling(resolved: Any) -> tuple[SandboxManager, Any, Any]:
    docker, calls = reenroll._docker()
    pm = reenroll._proxy()
    pm.llm_proxy_url = _PROXY
    mgr = reenroll._manager(docker, pm)
    if isinstance(resolved, Exception):
        mgr._credential_resolver.resolve_llm_for_job = AsyncMock(side_effect=resolved)
    else:
        mgr._credential_resolver.resolve_llm_for_job = AsyncMock(return_value=resolved)
    return mgr, pm, calls


async def _reenroll(mgr: SandboxManager, **kwargs: Any) -> None:
    try:
        await mgr.reenroll(
            "cid-1",
            job_id="job-1",
            repo="acme/widgets",
            forge="github",
            xai_credential_id=None,
            context_scope=None,
            **kwargs,
        )
    except TypeError as exc:
        pytest.fail(f"reenroll takes no provider credentials, so a resumed job's sandbox has no route: {exc}")


async def test_reenroll_gives_a_resumed_sandbox_its_providers_servers_again(dns) -> None:
    mgr, pm, calls = _reenrolling({"openai": _key("openai", "openai-secret-key-value")})
    await _reenroll(mgr, provider_credential_ids={"openai": "cred-openai"})
    assert pm.enroll_sandbox.await_args.kwargs.get("provider_servers") == {"openai": _server("openai")}, (
        "the resumed sandbox lost its route to its provider"
    )
    mgr._credential_resolver.resolve_llm_for_job.assert_awaited_once_with("acme/widgets", ("openai",), job_id="job-1")
    assert calls.writes(LOCAL_LLM_PROXY_TOKEN_REL) == [reenroll.NEW_TOKEN.encode()], "the bearer file not rewritten"


async def test_reenroll_of_a_job_on_no_hosted_provider_names_none(dns) -> None:
    mgr, pm, calls = _reenrolling({"openai": _key("openai", "openai-secret-key-value")})
    await _reenroll(mgr)
    assert "provider_servers" not in pm.enroll_sandbox.await_args.kwargs
    mgr._credential_resolver.resolve_llm_for_job.assert_not_awaited()
    assert calls.writes(LOCAL_LLM_PROXY_TOKEN_REL) == []


async def test_reenroll_whose_provider_cannot_be_had_leaves_the_job_paused(dns) -> None:
    mgr, pm, _calls = _reenrolling({})
    with pytest.raises(SandboxReenrollError, match="model providers") as exc:
        await _reenroll(mgr, provider_credential_ids={"openai": "cred-openai"})
    assert exc.value.error_code == ErrorCode.SANDBOX_INIT
    pm.enroll_sandbox.assert_not_awaited()


async def test_reenroll_names_a_provider_credential_that_changed(dns, capsys) -> None:
    mgr, pm, _calls = _reenrolling({"openai": _key("openai", "openai-secret-key-value")})
    await _reenroll(mgr, provider_credential_ids={"openai": "cred-before"})
    assert pm.enroll_sandbox.await_args.kwargs.get("provider_servers") == {"openai": _server("openai")}
    assert "sandbox_reenroll_provider_credential_changed" in capsys.readouterr().out


@pytest.mark.parametrize("networks", ["sandbox-restricted\nsandbox-internet\n", "sandbox-restricted\n"])
async def test_a_sandbox_created_with_a_hosted_provider_s_key_is_closed_and_refused(networks: str) -> None:
    """A release before ADR 0033 put the provider's key in the env, which cannot change: such a sandbox is not
    resumed, and the job is dispatched again."""
    docker, _calls = reenroll._docker(env_names="PATH\nEMBRY0_LLM_OPENAI_API_KEY\n", networks=networks)
    pm = reenroll._proxy()
    pm.llm_proxy_url = _PROXY
    pm.internet_proxies = lambda: ["git-proxy"]
    mgr = reenroll._manager(docker, pm)
    mgr._runtime.attach_policy = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(SandboxInitError) as refused:
        await _reenroll(mgr, provider_credential_ids={"openai": "cred-openai"})
    assert refused.value.error_code == ErrorCode.SANDBOX_ROUTE_RETIRED, refused.value
    assert not isinstance(refused.value, SandboxReenrollError), "the job was left paused, to be refused again"
    assert "EMBRY0_LLM_OPENAI_API_KEY" in str(refused.value) and "dispatch the job again" in str(refused.value)
    pm.enroll_sandbox.assert_not_awaited()
    if "sandbox-internet" in networks:
        mgr._runtime.attach_policy.assert_awaited_once()
        assert mgr._runtime.attach_policy.await_args.kwargs["allowed_destinations"] == []
    else:
        mgr._runtime.attach_policy.assert_not_awaited()
