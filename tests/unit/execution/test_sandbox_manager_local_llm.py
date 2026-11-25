"""SandboxManager and the local-llm-proxy: the custom provider without its key, its address or an egress hole.

Every sandbox on the custom provider reaches its credential's server through the proxy, enrolled with that server
alone (ADR 0032); there is no route around it.
"""

from unittest.mock import AsyncMock

import pytest

from embry0.execution.auth_provider import env_override_refusal
from embry0.execution.proxy import local_llm_proxy
from embry0.execution.sandbox_manager import SandboxInitError, SandboxManager, SandboxReenrollError
from embry0.safety.error_codes import ErrorCode
from embry0.sandbox.local_llm_token import LOCAL_LLM_CONTEXT_ENV, LOCAL_LLM_PROXY_FLAG, LOCAL_LLM_PROXY_TOKEN_REL
from embry0.services.credentials.resolver import CredentialUnavailableError
from tests.unit.execution import test_sandbox_reenroll as reenroll
from tests.unit.execution.test_sandbox_manager import (  # noqa: F401
    _stub_egress_unless_overridden,
    _stub_isolation_unless_overridden,
)
from tests.unit.execution.test_sandbox_manager_llm import _env, _manager
from tests.unit.execution.test_sandbox_manager_openai_compatible import _CUSTOM, _key

_PROXY = "http://local-llm-proxy:9108"
#: The server a sandbox on ``_CUSTOM`` is enrolled with: the credential's id, its API root and its key.
_ITS_SERVER = (
    local_llm_proxy.Upstream(credential_id="cred-custom", root="http://10.0.0.5:11434/v1", api_key="ollama-placeholder")
    if hasattr(local_llm_proxy, "Upstream")
    else "the credential's server (local_llm_proxy.Upstream, ADR 0032)"
)


def test_the_sandbox_gets_the_proxy_and_no_key_or_server_address() -> None:
    env: dict[str, str] = {}
    SandboxManager._inject_llm_env(env, {"custom": _CUSTOM}, local_proxy_url=_PROXY, context_tokens=65536)
    assert env["EMBRY0_LLM_CUSTOM_BASE_URL"] == f"{_PROXY}/v1"
    assert env[LOCAL_LLM_PROXY_FLAG] == "true"
    assert env[LOCAL_LLM_CONTEXT_ENV] == "65536"
    assert "EMBRY0_LLM_CUSTOM_API_KEY" not in env
    assert "10.0.0.5" not in str(env), "the server's address stays with the proxy"
    for key in (LOCAL_LLM_PROXY_FLAG, LOCAL_LLM_CONTEXT_ENV):
        assert env_override_refusal(key, "x") is not None, f"{key} must be reserved from repository override"


def test_without_the_proxy_the_sandbox_is_given_neither_key_nor_address() -> None:
    """There is no direct route any more: with no proxy to point at, the custom provider's env stays empty."""
    env: dict[str, str] = {}
    SandboxManager._inject_llm_env(env, {"custom": _CUSTOM}, local_proxy_url="")
    assert env == {}, f"the custom credential reached the sandbox's env without the proxy: {sorted(env)}"


def _proxied_manager(resolved):
    mgr, resolver, pm = _manager(resolved)
    pm.local_llm_proxy_url = _PROXY
    mgr._runtime.attach_policy = AsyncMock()  # type: ignore[method-assign]
    return mgr, resolver, pm


def _token_writes(mgr) -> list[bytes]:
    """What each write of the local-llm bearer file got on stdin; the bearer is never on the command line."""
    writes = [
        call.args[1]
        for call in mgr._docker.build_exec_cmd.call_args_list
        if LOCAL_LLM_PROXY_TOKEN_REL in " ".join(call.args[1])
    ]
    stdin = [c.kwargs["stdin_data"] for c in mgr._docker.run_cmd.call_args_list if c.kwargs.get("stdin_data")]
    return stdin[: len(writes)]


async def test_create_enrolls_the_sandbox_with_its_credential_s_server_and_writes_its_bearer() -> None:
    mgr, _, pm = _proxied_manager({"custom": _CUSTOM})
    container_id, token = await mgr.create(
        "job-1", repo="acme/widgets", llm_providers=["custom"], profile={"extra_networks": ["sandbox-internet"]}
    )
    assert pm.enroll_sandbox.await_args.kwargs.get("local_llm") == _ITS_SERVER, (
        "the sandbox must be enrolled with its credential's id, API root and key"
    )
    assert _env(mgr)["EMBRY0_LLM_CUSTOM_BASE_URL"] == f"{_PROXY}/v1"
    writes = _token_writes(mgr)
    assert writes == [token.encode()], "the bearer goes to the 0600 file the executor reads, on stdin"
    calls = mgr._docker.build_exec_cmd.call_args_list
    assert not any(token in word for c in calls for word in c.args[1]), "the bearer is never on an exec command line"


async def test_no_model_server_host_is_ever_an_egress_exception() -> None:
    mgr, _, _pm = _proxied_manager({"custom": _CUSTOM, "openai": _key("openai", "s")})
    await mgr.create(
        "job-1",
        repo="acme/widgets",
        llm_providers=["custom", "openai"],
        profile={"extra_networks": ["sandbox-internet"]},
        egress_allowlist=["registry.example.test"],
    )
    allowed = mgr._runtime.attach_policy.await_args.kwargs["allowed_destinations"]
    assert allowed == ["registry.example.test"], f"only the job's own exceptions are declared: {allowed}"


async def _refused(mgr) -> SandboxInitError | None:
    """The refusal of a custom job's sandbox, or None when it was created."""
    try:
        await mgr.create("job-1", repo="acme/widgets", llm_providers=["custom"])
    except SandboxInitError as exc:
        return exc
    return None


async def test_a_custom_job_does_not_start_without_the_proxy() -> None:
    mgr, _, pm = _manager({"custom": _CUSTOM})
    pm.local_llm_proxy_url = ""
    refused = await _refused(mgr)
    assert refused is not None, "a custom job started with no local-llm-proxy to reach its server through"
    assert "local-llm-proxy" in str(refused) and refused.error_code == ErrorCode.SANDBOX_INIT, refused
    assert not mgr._docker.build_run_cmd.called, "a container was started for a job with no route to its server"
    pm.enroll_sandbox.assert_not_awaited()


async def test_a_custom_credential_naming_no_server_does_not_start() -> None:
    nowhere = _key("custom", "k", {})
    mgr, _, pm = _proxied_manager({"custom": nowhere})
    refused = await _refused(mgr)
    assert refused is not None, "a custom job started on a credential that names no server"
    assert "names no server" in str(refused) and refused.error_code == ErrorCode.CREDENTIAL_UNAVAILABLE, refused
    assert not mgr._docker.build_run_cmd.called, "a container was started for a credential with no server"


async def test_a_sandbox_without_the_custom_provider_is_not_enrolled_with_the_proxy() -> None:
    mgr, _, pm = _proxied_manager({"deepseek": _key("deepseek", "s")})
    await mgr.create("job-1", repo="acme/widgets", llm_providers=["deepseek"])
    assert "local_llm" not in pm.enroll_sandbox.await_args.kwargs
    assert _token_writes(mgr) == []


async def test_a_failed_bearer_write_rolls_the_sandbox_back() -> None:
    mgr, _, pm = _proxied_manager({"custom": _CUSTOM})
    real_run = mgr._docker.run_cmd

    async def run(cmd, *args, **kwargs):
        if LOCAL_LLM_PROXY_TOKEN_REL in " ".join(map(str, cmd if isinstance(cmd, list) else [cmd])):
            raise RuntimeError("exec failed")
        return await real_run(cmd, *args, **kwargs)

    mgr._docker.run_cmd = AsyncMock(side_effect=run)
    mgr._docker.build_exec_cmd.side_effect = lambda container, argv, **_kw: ["exec", container, *argv]
    try:
        await mgr.create("job-1", repo="acme/widgets", llm_providers=["custom"])
    except Exception as exc:  # noqa: BLE001
        assert "enrollment failed" in str(exc).lower()
    else:
        raise AssertionError("a sandbox without its bearer must not be handed out")
    pm.unenroll_sandbox.assert_awaited()


# ---- re-enrollment of a resumed job's sandbox ----


def _reenrolling(resolved):
    docker, calls = reenroll._docker()
    pm = reenroll._proxy()
    pm.local_llm_proxy_url = _PROXY
    mgr = reenroll._manager(docker, pm)
    if isinstance(resolved, Exception):
        mgr._credential_resolver.resolve_llm_for_job = AsyncMock(side_effect=resolved)
    else:
        mgr._credential_resolver.resolve_llm_for_job = AsyncMock(return_value=resolved)
    return mgr, pm, calls


async def _reenroll(mgr, **kwargs):
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
        pytest.fail(f"reenroll takes no custom credential, so a resumed custom job's sandbox has no server: {exc}")


async def test_reenroll_gives_a_custom_job_s_sandbox_its_credential_s_server_again() -> None:
    mgr, pm, calls = _reenrolling({"custom": _CUSTOM})
    await _reenroll(mgr, custom_credential_id="cred-custom")
    assert pm.enroll_sandbox.await_args.kwargs.get("local_llm") == _ITS_SERVER, (
        "the resumed sandbox must be enrolled with the server its credential names"
    )
    mgr._credential_resolver.resolve_llm_for_job.assert_awaited_once_with("acme/widgets", ("custom",), job_id="job-1")
    assert calls.writes(LOCAL_LLM_PROXY_TOKEN_REL) == [reenroll.NEW_TOKEN.encode()], "the bearer file not rewritten"


async def test_reenroll_of_a_job_not_on_the_custom_provider_names_no_server() -> None:
    mgr, pm, calls = _reenrolling({"custom": _CUSTOM})
    await _reenroll(mgr)
    assert "local_llm" not in pm.enroll_sandbox.await_args.kwargs
    mgr._credential_resolver.resolve_llm_for_job.assert_not_awaited()
    assert calls.writes(LOCAL_LLM_PROXY_TOKEN_REL) == []


@pytest.mark.parametrize(
    "resolved",
    [CredentialUnavailableError("the key is quarantined"), {}, {"custom": _key("custom", "k", {})}],
    ids=["unavailable", "none-resolves", "no-server"],
)
async def test_reenroll_whose_server_cannot_be_had_leaves_the_job_paused(resolved) -> None:
    mgr, pm, _calls = _reenrolling(resolved)
    with pytest.raises(SandboxReenrollError, match="model server") as exc:
        await _reenroll(mgr, custom_credential_id="cred-custom")
    assert exc.value.error_code == ErrorCode.SANDBOX_INIT
    pm.enroll_sandbox.assert_not_awaited()


async def test_reenroll_names_a_custom_credential_that_changed(capsys) -> None:
    mgr, pm, _calls = _reenrolling({"custom": _CUSTOM})
    await _reenroll(mgr, custom_credential_id="cred-before")
    assert pm.enroll_sandbox.await_args.kwargs.get("local_llm") == _ITS_SERVER, "the credential that resolves now wins"
    assert "sandbox_reenroll_custom_credential_changed" in capsys.readouterr().out


async def test_reenroll_takes_the_server_the_credential_names_now_over_the_one_it_was_created_with() -> None:
    mgr, pm, _calls = _reenrolling({"custom": _CUSTOM})
    mgr._llm_credentials["cid-1"] = {"custom": _key("custom", "old-key", {"base_url": "http://10.0.0.9:11434/v1"})}
    await _reenroll(mgr, custom_credential_id="cred-custom")
    assert pm.enroll_sandbox.await_args.kwargs.get("local_llm") == _ITS_SERVER, (
        "the resumed sandbox was sent to the server it was created with, not the one its credential names now"
    )


async def test_reenroll_without_a_resolver_uses_the_credential_it_created_the_sandbox_with() -> None:
    mgr, pm, _calls = _reenrolling({})
    mgr._credential_resolver = None
    mgr._llm_credentials["cid-1"] = {"custom": _CUSTOM}
    await _reenroll(mgr, custom_credential_id="cred-custom")
    assert pm.enroll_sandbox.await_args.kwargs.get("local_llm") == _ITS_SERVER
