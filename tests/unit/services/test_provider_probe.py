"""RAV-1297 — live provider probe unit tests (httpx faked at the module seam)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from embry0.services import provider_probe as probe


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    def __init__(self, status: int | None = None, exc: Exception | None = None) -> None:
        self._status = status
        self._exc = exc
        self.last_headers: dict[str, str] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, headers=None):
        self.last_headers = headers
        if self._exc is not None:
            raise self._exc
        return _FakeResponse(self._status or 200)


def _patch_httpx(monkeypatch, *, status: int | None = None, exc: Exception | None = None) -> _FakeClient:
    client = _FakeClient(status=status, exc=exc)
    fake = SimpleNamespace(AsyncClient=lambda **_kw: client, HTTPError=httpx.HTTPError)
    monkeypatch.setattr(probe, "httpx", fake)
    return client


@pytest.mark.asyncio
async def test_anthropic_not_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    checks = await probe.probe_anthropic()
    assert [c.status for c in checks] == [probe.STATUS_NOT_CONFIGURED]


@pytest.mark.asyncio
async def test_anthropic_ok_via_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-not-real")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    client = _patch_httpx(monkeypatch, status=200)
    checks = await probe.probe_anthropic()
    assert checks[0].source == "api_key"
    assert checks[0].status == probe.STATUS_OK
    assert client.last_headers["x-api-key"] == "key-not-real"


@pytest.mark.asyncio
async def test_anthropic_oauth_uses_bearer_and_beta_header(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oat-not-real")
    client = _patch_httpx(monkeypatch, status=401)
    checks = await probe.probe_anthropic()
    assert checks[0].source == "oauth"
    assert checks[0].status == probe.STATUS_UNAUTHORIZED
    assert client.last_headers["Authorization"] == "Bearer oat-not-real"
    assert client.last_headers["anthropic-beta"] == "oauth-2025-04-20"


@pytest.mark.asyncio
async def test_unreachable_maps_transport_errors(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-not-real")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    _patch_httpx(monkeypatch, exc=httpx.ConnectError("boom"))
    checks = await probe.probe_anthropic()
    assert checks[0].status == probe.STATUS_UNREACHABLE
    # No credential text in the message.
    assert "key-not-real" not in checks[0].message


@pytest.mark.asyncio
async def test_xai_403_carries_entitlement_hint(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "xai-not-real")
    _patch_httpx(monkeypatch, status=403)
    checks = await probe.probe_xai()
    assert checks[0].status == probe.STATUS_ENTITLEMENT_REFUSED
    # RAV-1284 decoded semantics: X Premium+ is not API access.
    assert "SuperGrok" in checks[0].message


@pytest.mark.asyncio
async def test_xai_oauth_proxy_path_uses_refresher_token(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    client = _patch_httpx(monkeypatch, status=200)
    refresher = MagicMock()
    refresher.ensure_fresh = AsyncMock(return_value="access-not-real")
    checks = await probe.probe_xai(refresher)
    assert checks[0].source == "oauth_proxy"
    assert checks[0].status == probe.STATUS_OK
    assert client.last_headers["Authorization"] == "Bearer access-not-real"


@pytest.mark.asyncio
async def test_xai_refresh_failure_reports_unauthorized(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    refresher = MagicMock()
    refresher.ensure_fresh = AsyncMock(side_effect=RuntimeError("token endpoint said no"))
    checks = await probe.probe_xai(refresher)
    assert checks[0].status == probe.STATUS_UNAUTHORIZED
    assert "SuperGrok refresh failed" in checks[0].message


@pytest.mark.asyncio
async def test_xai_not_configured(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    checks = await probe.probe_xai(None)
    assert checks[0].status == probe.STATUS_NOT_CONFIGURED


@pytest.mark.asyncio
async def test_probe_llm_secret_anthropic_api_key_sends_x_api_key(monkeypatch):
    client = _patch_httpx(monkeypatch, status=200)
    check = await probe.probe_llm_secret("anthropic", "llm_api_key", "sk-ant-secret")
    assert check.status == probe.STATUS_OK
    assert client.last_headers["x-api-key"] == "sk-ant-secret"
    assert client.last_headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
async def test_probe_llm_secret_anthropic_oauth_sends_bearer_and_beta(monkeypatch):
    client = _patch_httpx(monkeypatch, status=200)
    check = await probe.probe_llm_secret("anthropic", "llm_oauth", "oat-secret")
    assert check.source == "oauth" and check.status == probe.STATUS_OK
    assert client.last_headers["Authorization"] == "Bearer oat-secret"
    assert client.last_headers["anthropic-beta"] == "oauth-2025-04-20"


@pytest.mark.asyncio
async def test_probe_llm_secret_xai_api_key_sends_bearer(monkeypatch):
    client = _patch_httpx(monkeypatch, status=401)
    check = await probe.probe_llm_secret("xai", "llm_api_key", "xai-secret")
    assert check.status == probe.STATUS_UNAUTHORIZED
    assert client.last_headers["Authorization"] == "Bearer xai-secret"
    assert "xai-secret" not in check.message


@pytest.mark.asyncio
async def test_probe_llm_secret_xai_oauth_sends_the_access_token(monkeypatch):
    """The caller refreshed first. This probe sends that access token, not the refresh payload."""
    client = _patch_httpx(monkeypatch, status=200)
    check = await probe.probe_llm_secret("xai", "llm_oauth", "access-token-value")
    assert check.source == "oauth" and check.status == probe.STATUS_OK
    assert client.last_headers["Authorization"] == "Bearer access-token-value"
    assert "access-token-value" not in check.message


def test_overall_status_ok_wins():
    checks = [
        probe.ProbeCheck("api_key", probe.STATUS_UNAUTHORIZED, "x"),
        probe.ProbeCheck("oauth", probe.STATUS_OK, "y"),
    ]
    assert probe.overall_status(checks) == probe.STATUS_OK
    assert probe.overall_status([checks[0]]) == probe.STATUS_UNAUTHORIZED
    assert probe.overall_status([]) == probe.STATUS_NOT_CONFIGURED


@pytest.mark.parametrize("host", ["2130706433", "0x7f000001", "127.1", "169.254.43518", "localtest.me"])
async def test_a_custom_server_on_this_machine_or_link_local_is_refused_unprobed(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A credential's check (the API's, and the startup check) probes its server with its key: never this machine
    or link-local, whatever the spelling or the name (ADR 0032)."""
    from tests.fixtures.model_servers import fake_dns

    fake_dns(monkeypatch, {"localtest.me": ["127.0.0.1"]})

    class Refuse:
        def __init__(self, *_a: object, **_k: object) -> None:
            raise AssertionError(f"the probe opened a client to {host}")

    monkeypatch.setattr(probe.httpx, "AsyncClient", Refuse)
    check = await probe.probe_llm_secret("custom", "llm_api_key", "the-key", base_url=f"http://{host}:8000/v1")
    assert check.status == getattr(probe, "STATUS_REFUSED", "refused"), f"{host} was probed: {check}"
    assert "the-key" not in check.message
