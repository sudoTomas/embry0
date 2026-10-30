"""Tests for GET /repos/{owner}/{repo}/-/environment/detect token routing (RAV-1301).

The detect endpoint reads .env templates via the GitHub contents API; it must
route the token per-owner (fine-grained PATs are single-owner) instead of
always sending the default token.
"""

import os
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import environment as environment_module
from embry0.config import Embry0Config


@pytest.fixture(autouse=True)
def _clean_owner_tokens(monkeypatch):
    for key in [k for k in os.environ if k.startswith("GITHUB_TOKEN__")]:
        monkeypatch.delenv(key)


class _CapturingClient:
    """Fake httpx.AsyncClient recording per-request headers; always 200.

    Detection routes through GitHubClient now (#158), which issues calls via
    ``request`` rather than ``get`` — hence both entry points here.
    """

    captured_headers: list[dict] = []

    def __init__(self, timeout=None, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def aclose(self):
        return None

    async def request(self, method, url, *, headers=None, params=None, json=None, timeout=None):
        return await self.get(url, headers=headers)

    async def get(self, url, headers=None):
        type(self).captured_headers.append(dict(headers or {}))

        class _Resp:
            status_code = 200
            text = "API_KEY=\n"

        return _Resp()


@pytest.fixture
def app():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="tok-default")
    app = create_app(config)
    env_repo = AsyncMock()
    env_repo.get_global = AsyncMock(return_value=[])
    env_repo.get_repo = AsyncMock(return_value=[])
    app.state.env_repo = env_repo
    return app


async def _detect(app, owner_repo: str):
    _CapturingClient.captured_headers = []
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/v1/repos/{owner_repo}/-/environment/detect")
    assert resp.status_code == 200
    return _CapturingClient.captured_headers


@pytest.mark.asyncio
async def test_detect_routes_token_per_owner(app, monkeypatch):
    monkeypatch.setattr(environment_module.httpx, "AsyncClient", _CapturingClient)
    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "tok-acme")
    headers = await _detect(app, "acme-corp/widgets")
    assert headers and headers[0]["Authorization"] == "Bearer tok-acme"


@pytest.mark.asyncio
async def test_detect_falls_back_to_default_token(app, monkeypatch):
    monkeypatch.setattr(environment_module.httpx, "AsyncClient", _CapturingClient)
    headers = await _detect(app, "someorg/repo")
    assert headers and headers[0]["Authorization"] == "Bearer tok-default"


@pytest.mark.asyncio
async def test_detect_answers_409_when_the_pinned_credential_is_unavailable(app, monkeypatch):
    from embry0.services.credentials.resolver import CredentialUnavailableError

    monkeypatch.setattr(environment_module.httpx, "AsyncClient", _CapturingClient)
    resolver = AsyncMock()
    resolver.resolve_forge = AsyncMock(side_effect=CredentialUnavailableError("credential 'ci' is pinned but disabled"))
    app.state.credential_resolver = resolver
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/repos/acme/widgets/-/environment/detect")
    assert resp.status_code == 409, resp.text
    assert "pinned" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_bootstrap_route_hands_the_app_resolver_to_verification(app, monkeypatch):
    from embry0.services.bootstrap import BootstrapError

    seen = {}

    async def _fake_verify(repo, token, credential_resolver=None):
        seen["resolver"] = credential_resolver
        raise BootstrapError("stop after verification")

    monkeypatch.setattr("embry0.services.bootstrap._verify_github_repo", _fake_verify)
    app.state.credential_resolver = object()
    app.state.qa_configs_repo = AsyncMock()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as client:
        resp = await client.post("/api/v1/repos/bootstrap", json={"repo": "acme/widgets"})
    assert resp.status_code == 400, resp.text
    assert seen["resolver"] is app.state.credential_resolver
