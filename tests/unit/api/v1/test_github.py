"""Tests for /api/v1/github/repos."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from athanor.api.app import create_app
from athanor.config import AthanorConfig


@pytest.fixture
def app_with_token():
    config = AthanorConfig(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="ghp_test_token")
    return create_app(config)


@pytest.fixture
def app_without_token():
    config = AthanorConfig(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True, github_token="")
    return create_app(config)


@pytest.mark.asyncio
async def test_list_repos_returns_400_when_token_missing(app_without_token):
    transport = ASGITransport(app=app_without_token)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/github/repos")
    assert resp.status_code == 400
    assert "GITHUB_TOKEN" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_repos_returns_repos_from_github(app_with_token):
    """Mocks httpx.AsyncClient so we don't actually hit api.github.com."""
    fake_repos = [
        {
            "full_name": "sudoTomas/athanor",
            "description": "Self-hosted issue-to-PR orchestrator.",
            "private": True,
            "html_url": "https://github.com/sudoTomas/athanor",
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

    with patch("athanor.api.v1.github.httpx.AsyncClient", return_value=mock_client):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/github/repos")

    assert resp.status_code == 200
    body = resp.json()
    assert "repos" in body
    assert len(body["repos"]) == 2
    assert body["repos"][0]["full_name"] == "sudoTomas/athanor"
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

    with patch("athanor.api.v1.github.httpx.AsyncClient", return_value=mock_client):
        transport = ASGITransport(app=app_with_token)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/github/repos")

    assert resp.status_code == 401
    assert "401" in resp.json()["detail"]
