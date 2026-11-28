"""SandboxManager and the llm-proxy: the custom provider without its key, its address or an egress hole.

Every sandbox on the custom provider reaches its credential's server through the proxy, enrolled with that server
alone (ADR 0032); there is no route around it.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from embry0.execution.auth_provider import env_override_refusal
from embry0.execution.proxy import llm_proxy
from embry0.execution.sandbox_manager import SandboxInitError, SandboxManager, SandboxReenrollError
from embry0.safety.error_codes import ErrorCode
from embry0.sandbox.local_llm_token import LOCAL_LLM_PROXY_FLAG, LOCAL_LLM_PROXY_TOKEN_REL
from embry0.services.credentials.resolver import CredentialUnavailableError
from tests.fixtures.model_servers import fake_dns
from tests.unit.execution import test_sandbox_reenroll as reenroll
from tests.unit.execution.test_sandbox_manager import (  # noqa: F401
    _stub_egress_unless_overridden,
    _stub_isolation_unless_overridden,
)
from tests.unit.execution.test_sandbox_manager_llm import _env, _manager
from tests.unit.execution.test_sandbox_manager_openai_compatible import _CUSTOM, _key

_PROXY = "http://llm-proxy:9108"


#: The server a sandbox on ``_CUSTOM`` is enrolled with: the credential's id, its API root and its key.
def _its_server() -> object:
    try:
        return llm_proxy.Upstream(
            credential_id="cred-custom",
            root="http://10.0.0.5:11434/v1",
            api_key="ollama-placeholder",
            address="10.0.0.5",
        )
    except (AttributeError, TypeError):
        return "the credential's server with its checked address (llm_proxy.Upstream, ADR 0032)"


_ITS_SERVER = _its_server()


def test_the_sandbox_gets_the_proxy_and_no_key_or_server_address() -> None:
    env: dict[str, str] = {}
    SandboxManager._inject_llm_env(env, {"custom": _CUSTOM}, local_proxy_url=_PROXY)
    assert env == {"EMBRY0_LLM_CUSTOM_BASE_URL": f"{_PROXY}/v1", LOCAL_LLM_PROXY_FLAG: "true"}, (
        f"the sandbox gets the proxy and its flag alone: no key, no address, no window (the catalogue's, per run): "
        f"{sorted(env)}"
    )
    assert env_override_refusal(LOCAL_LLM_PROXY_FLAG, "x") is not None, "the flag must be reserved from override"


def test_without_the_proxy_the_sandbox_is_given_neither_key_nor_address() -> None:
    """There is no direct route any more: with no proxy to point at, the custom provider's env stays empty."""
    env: dict[str, str] = {}
    SandboxManager._inject_llm_env(env, {"custom": _CUSTOM}, local_proxy_url="")
    assert env == {}, f"the custom credential reached the sandbox's env without the proxy: {sorted(env)}"


def _proxied_manager(resolved):
    mgr, resolver, pm = _manager(resolved)
    pm.llm_proxy_url = _PROXY
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


async def test_no_model_server_host_is_ever_an_egress_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns(monkeypatch, {"api.openai.com": ["162.159.140.245"]})  # reached through the proxy too (ADR 0033)
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
    pm.llm_proxy_url = ""
    refused = await _refused(mgr)
    assert refused is not None, "a custom job started with no llm-proxy to reach its server through"
    assert "llm-proxy" in str(refused) and refused.error_code == ErrorCode.SANDBOX_INIT, refused
    assert not mgr._docker.build_run_cmd.called, "a container was started for a job with no route to its server"
    pm.enroll_sandbox.assert_not_awaited()


async def test_a_custom_job_refused_while_the_proxy_is_held_says_why() -> None:
    mgr, _, pm = _manager({"custom": _CUSTOM})
    pm.llm_proxy_url = ""
    pm.llm_proxy_held = (
        "llm-proxy is not started: ... (docker rm -f local-llm-proxy); llm-proxy is started once it is gone"
    )
    refused = await _refused(mgr)
    assert refused is not None and "docker rm -f local-llm-proxy" in str(refused), f"the refusal: {refused}"


async def test_a_custom_credential_naming_no_server_does_not_start() -> None:
    nowhere = _key("custom", "k", {})
    mgr, _, pm = _proxied_manager({"custom": nowhere})
    refused = await _refused(mgr)
    assert refused is not None, "a custom job started on a credential that names no server"
    assert "names no server" in str(refused) and refused.error_code == ErrorCode.CREDENTIAL_UNAVAILABLE, refused
    assert not mgr._docker.build_run_cmd.called, "a container was started for a credential with no server"


@pytest.mark.parametrize(
    "root",
    [
        "http://127.0.0.1:11434/v1",
        "http://169.254.169.254/v1",
        "http://localhost/v1",
        "http://2130706433:11434/v1",
        "http://0x7f000001/v1",
        "http://127.1/v1",
        "http://169.254.43518/v1",
        "https://localtest.me/v1",
        "https://127.0.0.1.nip.io/v1",
        "http://metadata.google.internal/v1",
        "http://nowhere.test/v1",
    ],
)
async def test_a_custom_credential_naming_this_machine_or_link_local_does_not_start(
    root: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The credential may come from the environment (CUSTOM_LLM_BASE_URL), which no API check saw, or name a host
    that resolves there: its every address is read, and a name that resolves to none is no server either."""
    fake_dns(
        monkeypatch,
        {
            "localtest.me": ["127.0.0.1"],
            "127.0.0.1.nip.io": ["127.0.0.1"],
            "metadata.google.internal": ["169.254.169.254"],
        },
    )
    mgr, _, pm = _proxied_manager({"custom": _key("custom", "k", {"base_url": root})})
    refused = await _refused(mgr)
    assert refused is not None, f"a custom job started on a credential naming {root}"
    assert "does not reach" in str(refused) and refused.error_code == ErrorCode.CREDENTIAL_UNAVAILABLE, refused
    assert not mgr._docker.build_run_cmd.called, "a container was started for a server embry0 does not reach"


async def test_a_server_name_is_pinned_to_the_address_it_was_checked_at(monkeypatch: pytest.MonkeyPatch) -> None:
    """The proxy connects to the address the orchestrator resolved and checked, never to the name again."""
    fake_dns(monkeypatch, {"gpu-box.lan": ["10.0.0.9"]})
    mgr, _, pm = _proxied_manager({"custom": _key("custom", "k", {"base_url": "https://gpu-box.lan:8443/v1"})})
    await mgr.create("job-1", repo="acme/widgets", llm_providers=["custom"])
    server = pm.enroll_sandbox.await_args.kwargs.get("local_llm")
    got = (getattr(server, "root", None), getattr(server, "address", None))
    assert got == ("https://gpu-box.lan:8443/v1", "10.0.0.9"), (
        f"the sandbox must be enrolled with the name and its checked address: {server}"
    )


async def test_a_sandbox_without_the_custom_provider_is_not_enrolled_with_the_proxy() -> None:
    """Nor with no other chat-completions provider (a hosted one's server: test_sandbox_manager_provider_servers.py)."""
    mgr, _, pm = _proxied_manager({"anthropic": _key("anthropic", "s")})
    await mgr.create("job-1", repo="acme/widgets", llm_providers=["anthropic"])
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
    pm.llm_proxy_url = _PROXY
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


@pytest.mark.parametrize(
    "env_names",
    ["PATH\nEMBRY0_LLM_CUSTOM_API_KEY\nEMBRY0_LLM_CUSTOM_BASE_URL\n", "PATH\nEMBRY0_LLM_CUSTOM_BASE_URL\n"],
    ids=["key-and-address", "address-alone"],
)
@pytest.mark.parametrize("networks", ["sandbox-restricted\nsandbox-internet\n", "sandbox-restricted\n"])
async def test_a_sandbox_with_the_retired_direct_route_is_closed_and_refused(env_names: str, networks: str) -> None:
    """An older release put the custom provider's key and server address in the env, and the server's host in the
    egress exceptions. Such a sandbox is not resumed: its egress is closed to every private address, then the
    resume fails, telling the person to dispatch the job again."""
    docker, _calls = reenroll._docker(env_names=env_names, networks=networks)
    pm = reenroll._proxy()
    pm.llm_proxy_url = _PROXY
    pm.internet_proxies = lambda: ["git-proxy"]
    mgr = reenroll._manager(docker, pm)
    mgr._runtime.attach_policy = AsyncMock()  # type: ignore[method-assign]
    try:
        await mgr.reenroll(
            "cid-1", job_id="job-1", repo="acme/widgets", forge="github", xai_credential_id=None, context_scope=None
        )
    except SandboxInitError as exc:
        refused: SandboxInitError | None = exc
    else:
        refused = None
    assert refused is not None, "a sandbox with the custom provider's key or address in its env was resumed"
    route_retired = getattr(ErrorCode, "SANDBOX_ROUTE_RETIRED", None)
    assert route_retired is not None and refused.error_code == route_retired, refused
    assert "dispatch the job again" in str(refused), refused
    assert not isinstance(refused, SandboxReenrollError), (
        "the job was left paused, to be refused again at every resume, instead of failing"
    )
    pm.enroll_sandbox.assert_not_awaited()
    if "sandbox-internet" in networks:
        mgr._runtime.attach_policy.assert_awaited_once_with(
            "cid-1", allowed_destinations=[], reachable_containers=["git-proxy"]
        )
    else:
        mgr._runtime.attach_policy.assert_not_awaited()


async def test_a_sandbox_from_the_proxied_route_is_resumed_and_its_policy_left_alone() -> None:
    """A sandbox the older release created through LOCAL_LLM_UPSTREAM has the proxy as its address and no key: it
    resumes, its own declared egress targets kept."""
    docker, _calls = reenroll._docker(
        env_names="PATH\nEMBRY0_LLM_CUSTOM_BASE_URL\nEMBRY0_LLM_CUSTOM_VIA_PROXY\n", networks="sandbox-internet\n"
    )
    pm = reenroll._proxy()
    pm.llm_proxy_url = _PROXY
    mgr = reenroll._manager(docker, pm)
    mgr._runtime.attach_policy = AsyncMock()  # type: ignore[method-assign]
    mgr._credential_resolver.resolve_llm_for_job = AsyncMock(return_value={"custom": _CUSTOM})
    await _reenroll(mgr, custom_credential_id="cred-custom")
    pm.enroll_sandbox.assert_awaited_once()
    mgr._runtime.attach_policy.assert_not_awaited()


@pytest.mark.parametrize("failure", ["env", "egress"])
async def test_a_retired_route_that_cannot_be_checked_or_closed_leaves_the_job_paused(failure: str) -> None:
    env_names: Any = RuntimeError("daemon restarting") if failure == "env" else "EMBRY0_LLM_CUSTOM_API_KEY\n"
    docker, _calls = reenroll._docker(env_names=env_names, networks="sandbox-internet\n")
    pm = reenroll._proxy()
    mgr = reenroll._manager(docker, pm)
    mgr._runtime.attach_policy = AsyncMock(side_effect=RuntimeError("iptables busy"))  # type: ignore[method-assign]
    try:
        await mgr.reenroll(
            "cid-1", job_id="job-1", repo="acme/widgets", forge="github", xai_credential_id=None, context_scope=None
        )
    except SandboxReenrollError:
        paused = True
    else:
        paused = False
    assert paused, f"a sandbox whose route could not be {'checked' if failure == 'env' else 'closed'} was resumed"
    pm.enroll_sandbox.assert_not_awaited()


async def test_reenroll_without_a_resolver_uses_the_credential_it_created_the_sandbox_with() -> None:
    mgr, pm, _calls = _reenrolling({})
    mgr._credential_resolver = None
    mgr._llm_credentials["cid-1"] = {"custom": _CUSTOM}
    await _reenroll(mgr, custom_credential_id="cred-custom")
    assert pm.enroll_sandbox.await_args.kwargs.get("local_llm") == _ITS_SERVER


def test_only_the_env_s_names_leave_the_daemon() -> None:
    """The check reads which variables a sandbox has, never their values: the custom key among them."""
    from embry0.execution.docker_client import DockerClient

    build = getattr(DockerClient(), "build_inspect_env_names_cmd", None)
    assert build is not None, "nothing reads which variables a sandbox's env has without their values"
    cmd = build("cid-1")
    template = cmd[cmd.index("--format") + 1]
    assert template == '{{range .Config.Env}}{{println (index (split . "=") 0)}}{{end}}', template
    assert cmd[-1] == "cid-1"


def _limits(max_request_bytes: int, request_timeout_seconds: int) -> AsyncMock:
    """A reader of the settings document's local_models section, as production passes the manager (#174)."""
    from embry0.settings.sections.local_models import LocalModelsSettings

    return AsyncMock(
        return_value=LocalModelsSettings(
            max_request_bytes=max_request_bytes, request_timeout_seconds=request_timeout_seconds
        )
    )


def _enrolled_limits(pm: Any) -> tuple[Any, Any]:
    """The limits the sandbox was enrolled with, for every server it is enrolled for (``enroll_sandbox(limits=)``)."""
    limits = pm.enroll_sandbox.await_args.kwargs.get("limits")
    return getattr(limits, "max_request_bytes", None), getattr(limits, "timeout_seconds", None)


async def test_a_sandbox_is_enrolled_under_the_local_models_limits_read_when_it_is_created() -> None:
    """local_models.max_request_bytes and request_timeout_seconds go with each enrollment, read at that moment, so
    an admin's change reaches the next sandbox with no restart."""
    mgr, _, pm = _proxied_manager({"custom": _CUSTOM})
    mgr._local_models = _limits(2048, 60)
    await mgr.create("job-1", repo="acme/widgets", llm_providers=["custom"])
    assert _enrolled_limits(pm) == (2048, 60.0), f"the sandbox's limits are the section's: {_enrolled_limits(pm)}"
    mgr._local_models = _limits(4096, 120)
    await mgr.create("job-2", repo="acme/widgets", llm_providers=["custom"])
    assert _enrolled_limits(pm) == (4096, 120.0), "a change of the section did not reach the next sandbox"


async def test_without_a_reader_a_sandbox_gets_the_section_s_defaults() -> None:
    from embry0.settings.sections.local_models import LOCAL_MODELS

    mgr, _, pm = _proxied_manager({"custom": _CUSTOM})
    await mgr.create("job-1", repo="acme/widgets", llm_providers=["custom"])
    defaults = LOCAL_MODELS.model()
    assert _enrolled_limits(pm) == (defaults.max_request_bytes, float(defaults.request_timeout_seconds))


async def test_a_resumed_sandbox_is_enrolled_under_the_limits_in_force_now() -> None:
    mgr, pm, _calls = _reenrolling({"custom": _CUSTOM})
    mgr._local_models = _limits(2048, 60)
    await _reenroll(mgr, custom_credential_id="cred-custom")
    assert _enrolled_limits(pm) == (2048, 60.0), f"a resumed sandbox kept no limits of now: {_enrolled_limits(pm)}"


def _budget(refuse: bool) -> AsyncMock:
    """A reader of the settings document's budget section, as production passes the manager."""
    from embry0.settings.sections.budget import BudgetSettings

    try:
        return AsyncMock(return_value=BudgetSettings(refuse_calls_outside_runs=refuse))
    except Exception as exc:  # noqa: BLE001 — a section without the key
        pytest.fail(f"the budget section has no refuse_calls_outside_runs: {exc}")


async def test_a_sandbox_is_enrolled_holding_it_to_runs_as_the_budget_section_says_when_it_is_created() -> None:
    mgr, _, pm = _proxied_manager({"custom": _CUSTOM})
    for refuse in (False, True):
        mgr._budget = _budget(refuse)
        await mgr.create(f"job-{refuse}", repo="acme/widgets", llm_providers=["custom"])
        named = pm.enroll_sandbox.await_args.kwargs.get("require_run")
        assert named is refuse, f"refuse_calls_outside_runs {refuse} enrolled the sandbox with {named}"


async def test_without_a_reader_a_sandbox_is_held_to_runs() -> None:
    """The section's default: every model call a sandbox makes is an agent run's."""
    mgr, _, pm = _proxied_manager({"custom": _CUSTOM})
    await mgr.create("job-1", repo="acme/widgets", llm_providers=["custom"])
    named = pm.enroll_sandbox.await_args.kwargs.get("require_run")
    assert named is True, f"a sandbox with no reader was enrolled with require_run {named}"


async def test_a_resumed_sandbox_is_held_to_runs_as_the_section_says_now() -> None:
    mgr, pm, _calls = _reenrolling({"custom": _CUSTOM})
    mgr._budget = _budget(False)
    await _reenroll(mgr, custom_credential_id="cred-custom")
    named = pm.enroll_sandbox.await_args.kwargs.get("require_run")
    assert named is False, f"a resumed sandbox was enrolled with require_run {named}"
