"""SandboxManager and the local-llm-proxy: the custom provider without its key, its address or an egress hole."""

from unittest.mock import AsyncMock

from embry0.execution.auth_provider import env_override_refusal
from embry0.execution.sandbox_manager import SandboxManager
from embry0.sandbox.local_llm_token import LOCAL_LLM_CONTEXT_ENV, LOCAL_LLM_PROXY_FLAG, LOCAL_LLM_PROXY_TOKEN_REL
from tests.unit.execution.test_sandbox_manager import (  # noqa: F401
    _stub_egress_unless_overridden,
    _stub_isolation_unless_overridden,
)
from tests.unit.execution.test_sandbox_manager_llm import _env, _manager
from tests.unit.execution.test_sandbox_manager_openai_compatible import _CUSTOM, _key

_PROXY = "http://local-llm-proxy:9108"


def test_through_the_proxy_the_sandbox_gets_the_proxy_and_no_key_or_server_address() -> None:
    env: dict[str, str] = {}
    SandboxManager._inject_llm_env(env, {"custom": _CUSTOM}, local_proxy_url=_PROXY, context_tokens=65536)
    assert env["EMBRY0_LLM_CUSTOM_BASE_URL"] == f"{_PROXY}/v1"
    assert env[LOCAL_LLM_PROXY_FLAG] == "true"
    assert env[LOCAL_LLM_CONTEXT_ENV] == "65536"
    assert "EMBRY0_LLM_CUSTOM_API_KEY" not in env
    assert "10.0.0.5" not in str(env), "the server's address stays with the proxy"
    for key in (LOCAL_LLM_PROXY_FLAG, LOCAL_LLM_CONTEXT_ENV):
        assert env_override_refusal(key, "x") is not None, f"{key} must be reserved from repository override"


def test_through_the_proxy_no_egress_exception_is_declared() -> None:
    resolved = {"custom": _CUSTOM, "openai": _key("openai", "s")}
    assert SandboxManager._llm_egress_destinations(resolved, via_proxy=True) == []
    assert SandboxManager._llm_egress_destinations(resolved) == ["10.0.0.5"]


def _proxied_manager(resolved):
    mgr, resolver, pm = _manager(resolved)
    pm.local_llm_proxy_url = _PROXY
    mgr._runtime.attach_policy = AsyncMock()  # type: ignore[method-assign]
    return mgr, resolver, pm


def _token_writes(mgr) -> list[list[str]]:
    return [
        call.args[1]
        for call in mgr._docker.build_exec_cmd.call_args_list
        if LOCAL_LLM_PROXY_TOKEN_REL in " ".join(call.args[1])
    ]


async def test_create_enrolls_with_the_proxy_and_writes_its_bearer() -> None:
    mgr, _, pm = _proxied_manager({"custom": _CUSTOM})
    container_id, token = await mgr.create(
        "job-1", repo="acme/widgets", llm_providers=["custom"], profile={"extra_networks": ["sandbox-internet"]}
    )
    assert pm.enroll_sandbox.await_args.kwargs.get("local_llm") is True
    assert _env(mgr)["EMBRY0_LLM_CUSTOM_BASE_URL"] == f"{_PROXY}/v1"
    assert mgr._runtime.attach_policy.await_args.kwargs["allowed_destinations"] == []
    writes = _token_writes(mgr)
    assert len(writes) == 1 and token in writes[0][-1], "the bearer goes to the 0600 file the executor reads"


async def test_a_sandbox_without_the_custom_provider_is_not_enrolled_with_the_proxy() -> None:
    mgr, _, pm = _proxied_manager({"deepseek": _key("deepseek", "s")})
    await mgr.create("job-1", repo="acme/widgets", llm_providers=["deepseek"])
    assert "local_llm" not in pm.enroll_sandbox.await_args.kwargs
    assert _token_writes(mgr) == []


async def test_without_the_proxy_the_custom_provider_works_as_before() -> None:
    mgr, _, pm = _manager({"custom": _CUSTOM})
    pm.local_llm_proxy_url = ""
    mgr._runtime.attach_policy = AsyncMock()  # type: ignore[method-assign]
    await mgr.create(
        "job-1", repo="acme/widgets", llm_providers=["custom"], profile={"extra_networks": ["sandbox-internet"]}
    )
    assert _env(mgr)["EMBRY0_LLM_CUSTOM_API_KEY"] == "ollama-placeholder"
    assert "local_llm" not in pm.enroll_sandbox.await_args.kwargs


async def test_a_failed_bearer_write_rolls_the_sandbox_back() -> None:
    mgr, _, pm = _proxied_manager({"custom": _CUSTOM})
    real_run = mgr._docker.run_cmd

    async def run(cmd, *args, **kwargs):
        if LOCAL_LLM_PROXY_TOKEN_REL in " ".join(map(str, cmd if isinstance(cmd, list) else [cmd])):
            raise RuntimeError("exec failed")
        return await real_run(cmd, *args, **kwargs)

    mgr._docker.run_cmd = AsyncMock(side_effect=run)
    mgr._docker.build_exec_cmd.side_effect = lambda container, argv: ["exec", container, *argv]
    try:
        await mgr.create("job-1", repo="acme/widgets", llm_providers=["custom"])
    except Exception as exc:  # noqa: BLE001
        assert "enrollment failed" in str(exc).lower()
    else:
        raise AssertionError("a sandbox without its bearer must not be handed out")
    pm.unenroll_sandbox.assert_awaited()


async def test_reenroll_after_a_restart_reads_the_flag_from_the_container() -> None:
    mgr, _, pm = _proxied_manager({"custom": _CUSTOM})
    mgr._docker.build_inspect_has_env_cmd = lambda container, entry: ["inspect", container, entry]
    answers = {"inspect": "yes"}

    async def run(cmd, *args, **kwargs):
        return answers.get(cmd[0], "true") if isinstance(cmd, list) else "true"

    mgr._docker.run_cmd = AsyncMock(side_effect=run)
    assert await mgr._reached_local_llm("sandbox-job-1") is True
    answers["inspect"] = ""
    assert await mgr._reached_local_llm("sandbox-job-1") is False
    mgr._llm_credentials["sandbox-job-2"] = {"custom": _CUSTOM}
    assert await mgr._reached_local_llm("sandbox-job-2") is True, "this process's own record wins"
