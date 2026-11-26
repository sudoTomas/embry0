"""ProxyManager enrolls a sandbox with the server of each chat-completions provider its job uses, on the proxy's path
per provider, and refuses a proxy that does not confirm every one of them."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from embry0.execution.proxy.local_llm_proxy import Limits, Upstream
from embry0.execution.proxy.manager import ProxyManager


@pytest.fixture(autouse=True)
def _resolution(monkeypatch):
    names = {"model-box": "10.99.0.7"}

    def lookup(host: str) -> str:
        if host not in names:
            raise OSError(f"no such host {host}")
        return names[host]

    monkeypatch.setattr("embry0.execution.proxy.manager.socket.gethostbyname", lookup)


# Each with the address its caller resolved and checked (server_address.pin_server).
OPENAI = Upstream(credential_id="cred-o", root="https://api.openai.com/v1/", api_key="key-openai", address="10.0.1.1")
GROQ = Upstream(credential_id="cred-g", root="http://model-box:8000/v1", api_key="key-groq", address="10.99.0.7")
LOCAL = Upstream(credential_id="cred-c", root="http://model-box:9000/v1", api_key="key-local", address="10.99.0.7")
#: The sandbox's limits at every server it is enrolled for (#174).
LIMITS = Limits(max_request_bytes=2048, timeout_seconds=60.0)
_CONFIRMED = ("credential_id", "upstream", "address")


async def _enrolling_manager(monkeypatch, echo: Any = True) -> tuple[ProxyManager, list[dict[str, Any]]]:
    """A manager whose proxies answer as a proxy of this release does, or with ``echo`` for provider_servers."""
    pm = ProxyManager(docker=AsyncMock(), proxy_admin_token="x" * 20)
    pm._http = object()
    pm.xai_proxy_url = "http://xai-proxy:9106"
    pm.local_llm_proxy_url = "http://local-llm-proxy:9108"
    captured: list[dict[str, Any]] = []

    async def fake_exec(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        body = kwargs.get("body") or {}
        if kwargs["container"] != "local-llm-proxy" or kwargs["method"] != "POST":
            return {}
        answer: dict[str, Any] = {}
        if "local_llm" in body:
            server = body["local_llm"]
            answer["local_llm"] = {k: server[k] for k in _CONFIRMED}
        if "limits" in body:
            answer["limits"] = dict(body["limits"])
        if "provider_servers" in body and echo is not False:
            answer["provider_servers"] = (
                {p: {k: s[k] for k in _CONFIRMED} for p, s in body["provider_servers"].items()}
                if echo is True
                else echo
            )
        return answer

    monkeypatch.setattr(pm, "_exec_admin_request", fake_exec)
    return pm, captured


async def test_each_provider_s_server_goes_to_the_local_proxy_alone_at_its_pinned_address(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    token = await pm.enroll_sandbox("sb-1", provider_servers={"openai": OPENAI, "groq": GROQ}, limits=LIMITS)
    assert [c["container"] for c in captured] == ["xai-proxy", "local-llm-proxy"]
    local = next(c for c in captured if c["container"] == "local-llm-proxy")
    assert (local["port"], local["path"], local["body"]["sandbox_token"]) == (9108, "/admin/enroll", token)
    assert "local_llm" not in local["body"], "a sandbox with no custom server was sent one"
    # The root keeps its name (the Host, and for https the TLS name); the address is the one the caller checked.
    assert local["body"]["provider_servers"] == {
        "openai": {
            "credential_id": "cred-o",
            "upstream": "https://api.openai.com/v1",
            "address": "10.0.1.1",
            "api_key": "key-openai",
        },
        "groq": {
            "credential_id": "cred-g",
            "upstream": "http://model-box:8000/v1",
            "address": "10.99.0.7",
            "api_key": "key-groq",
        },
    }
    xai = next(c for c in captured if c["container"] == "xai-proxy")
    assert "provider_servers" not in xai["body"], "another proxy was sent a provider's key"


async def test_the_custom_server_and_the_providers_go_in_one_enrollment(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    await pm.enroll_sandbox("sb-1", local_llm=LOCAL, provider_servers={"openai": OPENAI}, limits=LIMITS)
    local = [c for c in captured if c["container"] == "local-llm-proxy"]
    assert len(local) == 1, "the sandbox was enrolled with the local proxy more than once"
    assert set(local[0]["body"]) >= {"local_llm", "provider_servers"}


_OK_OPENAI = {"credential_id": "cred-o", "upstream": "https://api.openai.com/v1", "address": "10.0.1.1"}
_OK_GROQ = {"credential_id": "cred-g", "upstream": "http://model-box:8000/v1", "address": "10.99.0.7"}


@pytest.mark.parametrize(
    "echo",
    [
        False,
        {},
        {"openai": _OK_OPENAI},
        {"openai": _OK_OPENAI, "groq": {**_OK_GROQ, "credential_id": "cred-x"}},
        {"openai": _OK_OPENAI, "groq": {**_OK_GROQ, "address": "10.99.0.8"}},
        {"openai": _OK_OPENAI, "groq": {k: v for k, v in _OK_GROQ.items() if k != "address"}},
        {
            "openai": _OK_OPENAI,
            "groq": _OK_GROQ,
            "deepseek": {"credential_id": "cred-d", "upstream": "https://api.deepseek.com", "address": "10.0.1.2"},
        },
    ],
    ids=["no-echo", "empty", "one-missing", "another-credential", "another-address", "no-address", "one-more"],
)
async def test_a_proxy_that_does_not_confirm_every_provider_is_refused(monkeypatch, echo):
    """An image from before the paths per provider takes the enrollment and keeps no provider; one that confirms
    anything but exactly what was sent could route the sandbox to a server it was not given."""
    pm, captured = await _enrolling_manager(monkeypatch, echo=echo)
    with pytest.raises(RuntimeError, match="did not confirm the sandbox's provider servers"):
        await pm.enroll_sandbox("sb-1", provider_servers={"openai": OPENAI, "groq": GROQ}, limits=LIMITS)
    revoked = [c for c in captured if c["container"] == "local-llm-proxy" and c["method"] == "DELETE"]
    assert len(revoked) == 1 and revoked[0]["path"] == "/admin/enroll/sb-1", f"not revoked: {captured}"


async def test_the_custom_provider_is_never_a_provider_server(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    with pytest.raises(ValueError, match="custom"):
        await pm.enroll_sandbox("sb-1", provider_servers={"custom": LOCAL}, limits=LIMITS)
    assert captured == []


async def test_a_sandbox_on_a_provider_without_the_proxy_is_refused(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    pm.local_llm_proxy_url = ""
    with pytest.raises(RuntimeError, match="local-llm-proxy is not running"):
        await pm.enroll_sandbox("sb-1", provider_servers={"openai": OPENAI}, limits=LIMITS)
    assert captured == [], "a sandbox with no route to its provider was enrolled anywhere"


async def test_a_sandbox_with_no_provider_server_is_enrolled_as_before(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    await pm.enroll_sandbox("sb-1", provider_servers={})
    assert [c["container"] for c in captured] == ["xai-proxy"]


async def test_a_recreated_proxy_is_given_the_provider_servers_back(monkeypatch):
    """The servers travel in the enrollment, which the manager holds for a recreated proxy and sends it again."""
    pm = ProxyManager(docker=AsyncMock(), proxy_admin_token="x" * 20)
    pm._http = object()
    pm.local_llm_proxy_url = "http://local-llm-proxy:9108"
    sent: list[dict[str, Any]] = []

    async def once(**kwargs: Any) -> dict[str, Any]:
        sent.append(kwargs)
        body = kwargs.get("body") or {}
        servers = body.get("provider_servers", {})
        return {
            "provider_servers": {p: {k: s[k] for k in _CONFIRMED} for p, s in servers.items()},
            "limits": body.get("limits"),
        }

    monkeypatch.setattr(pm, "_exec_admin_once", once)
    await pm.enroll_sandbox("sb-1", provider_servers={"openai": OPENAI}, limits=LIMITS)
    sent.clear()
    await pm._replay("local-llm-proxy")
    replayed = [c["body"] for c in sent if c["path"] == "/admin/enroll"]
    assert len(replayed) == 1, f"the enrollment was not given back: {sent}"
    assert replayed[0]["provider_servers"]["openai"]["api_key"] == "key-openai"
    assert replayed[0]["limits"] == {"max_request_bytes": 2048, "timeout_seconds": 60.0}, (
        "the limits were not given back"
    )


async def test_a_sandbox_on_providers_alone_is_sent_its_limits(monkeypatch):
    """#174: one set of limits per sandbox, on every path it calls: a sandbox with no custom server has them too."""
    pm, captured = await _enrolling_manager(monkeypatch)
    await pm.enroll_sandbox("sb-1", provider_servers={"openai": OPENAI}, limits=LIMITS)
    local = next(c for c in captured if c["container"] == "local-llm-proxy")
    assert local["body"].get("limits") == {"max_request_bytes": 2048, "timeout_seconds": 60.0}, local["body"]


async def test_a_sandbox_on_providers_without_limits_is_refused(monkeypatch):
    pm, captured = await _enrolling_manager(monkeypatch)
    with pytest.raises(ValueError, match="limits"):
        await pm.enroll_sandbox("sb-1", provider_servers={"openai": OPENAI})
    assert captured == [], "a sandbox with no limits was enrolled anywhere"
