from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient

from athanor.execution.proxy.github_proxy import create_github_proxy_app

ADMIN = "test-admin-secret-not-real"
SANDBOX_TOKEN = "tok-test-" + "a" * 30  # >= 32 chars


@pytest.fixture
async def gh_client(aiohttp_client) -> TestClient:
    app = create_github_proxy_app(
        github_token="ghp_test_token_456",
        admin_token=ADMIN,
    )
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_health(gh_client: TestClient):
    resp = await gh_client.get("/health")
    assert resp.status == 200
    data = await resp.json()
    assert data["proxy"] == "github_api"


@pytest.mark.asyncio
async def test_proxy_injects_token(gh_client: TestClient):
    """Enrolled bearer requests should be forwarded with the PAT injected."""
    enroll = await gh_client.post(
        "/admin/enroll",
        json={"sandbox_id": "s1", "sandbox_token": SANDBOX_TOKEN},
        headers={"X-Admin-Token": ADMIN},
    )
    assert enroll.status == 200

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = b'{"number": 1}'
    mock_resp.headers = {"content-type": "application/json"}

    with patch.object(gh_client.app["http_client"], "request", return_value=mock_resp):
        resp = await gh_client.get(
            "/repos/owner/repo/issues/1",
            headers={"Authorization": f"Bearer {SANDBOX_TOKEN}"},
        )

    assert resp.status == 200
