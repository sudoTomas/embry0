"""Tests for /api/v1/github/repos."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app
from embry0.api.v1 import github as github_module
from embry0.config import Embry0Config


@pytest.fixture(autouse=True)
def _clean_owner_tokens(monkeypatch):
    for key in [k for k in os.environ if k.startswith("GITHUB_TOKEN__")]:
        monkeypatch.delenv(key)


@pytest.fixture
def app_with_token():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="ghp_test_token")
    return create_app(config)


@pytest.fixture
def app_without_token():
    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="")
    return create_app(config)


@pytest.mark.asyncio
async def test_list_repos_returns_400_when_token_missing(app_without_token):
    transport = ASGITransport(app=app_without_token)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/github/repos")
    assert resp.status_code == 400
    assert "No GitHub token configured" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_repos_returns_repos_from_github(app_with_token):
    """Mocks httpx.AsyncClient so we don't actually hit api.github.com."""
    fake_repos = [
        {
            "full_name": "sudoTomas/embry0",
            "description": "Self-hosted issue-to-PR orchestrator.",
            "private": True,
            "html_url": "https://github.com/sudoTomas/embry0",
            "default_branch": "main",
            "language": "Python",
            "open_issues_count": 3,
        },
        {
            "full_name": "sudoTomas/some-other",
            "description": None,
            "private": False,
            "html_url": "https://github.com/sudoTomas/some-other",
            "default_branch": "main",
            "language": None,
            "open_issues_count": 0,
        },
    ]
    mock_resp = MagicMock()
    mock_resp.json = MagicMock(return_value=fake_repos)
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("embry0.api.v1.github.httpx.AsyncClient", return_value=mock_client):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/github/repos")

    assert resp.status_code == 200
    body = resp.json()
    assert "repos" in body
    assert len(body["repos"]) == 2
    assert body["repos"][0]["full_name"] == "sudoTomas/embry0"
    assert body["repos"][0]["language"] == "Python"
    assert body["repos"][1]["language"] is None
    # Verify the GitHub API was called with the right params
    mock_client.get.assert_called_once()
    call_kwargs = mock_client.get.call_args
    assert "/user/repos" in call_kwargs[0][0]
    assert call_kwargs[1]["params"]["per_page"] == 100
    assert "owner" in call_kwargs[1]["params"]["affiliation"]


@pytest.mark.asyncio
async def test_list_repos_propagates_github_error(app_with_token):
    """A non-2xx from GitHub should surface as the same status code to the caller."""
    import httpx

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json = MagicMock(return_value={"message": "Bad credentials"})
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=mock_resp)
    )

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("embry0.api.v1.github.httpx.AsyncClient", return_value=mock_client):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/github/repos")

    assert resp.status_code == 401
    assert "401" in resp.json()["detail"]


def _repo(full_name: str) -> dict:
    return {
        "full_name": full_name,
        "description": None,
        "private": True,
        "html_url": f"https://github.com/{full_name}",
        "default_branch": "main",
        "language": "Python",
        "open_issues_count": 0,
    }


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "err",
                request=httpx.Request("GET", "https://api.github.com/user/repos"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


class _FakeClient:
    """Maps Authorization header -> canned response, mimicking httpx.AsyncClient."""

    responses_by_token: dict[str, object] = {}

    def __init__(self, headers=None, timeout=None):
        self._token = (headers or {}).get("Authorization", "").removeprefix("Bearer ")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None):
        return self.responses_by_token[self._token]


@pytest.mark.asyncio
async def test_list_repos_merges_and_dedups_across_tokens(monkeypatch):
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    _FakeClient.responses_by_token = {
        "tok-default": _FakeResponse([_repo("octo-org/embry0"), _repo("shared/overlap")]),
        "tok-rc": _FakeResponse([_repo("acme-corp/widgets"), _repo("shared/overlap")]),
    }
    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "tok-rc")
    result = await github_module._fetch_all_repos("tok-default", per_page=100, sort="updated")
    names = [r.full_name for r in result]
    assert names == ["acme-corp/widgets", "octo-org/embry0", "shared/overlap"]


@pytest.mark.asyncio
async def test_list_repos_skips_failing_token(monkeypatch):
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    _FakeClient.responses_by_token = {
        "tok-dead": _FakeResponse({"message": "Bad credentials"}, status_code=401),
        "tok-rc": _FakeResponse([_repo("acme-corp/widgets")]),
    }
    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "tok-rc")
    result = await github_module._fetch_all_repos("tok-dead", per_page=100, sort="updated")
    assert [r.full_name for r in result] == ["acme-corp/widgets"]


@pytest.mark.asyncio
async def test_list_repos_all_tokens_failing_raises(monkeypatch):
    monkeypatch.setattr(github_module.httpx, "AsyncClient", _FakeClient)
    _FakeClient.responses_by_token = {"tok-dead": _FakeResponse({"message": "no"}, status_code=401)}
    for key in [k for k in os.environ if k.startswith("GITHUB_TOKEN__")]:
        monkeypatch.delenv(key)
    with pytest.raises(Exception):
        await github_module._fetch_all_repos("tok-dead", per_page=100, sort="updated")
