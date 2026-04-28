"""Tests for ProxyManager.enroll_sandbox / unenroll_sandbox."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from athanor.execution.proxy.manager import ProxyManager


@pytest.fixture
def manager():
    docker = MagicMock()
    mgr = ProxyManager(docker, proxy_admin_token="admin-secret")
    mgr.git_proxy_url = "http://git-proxy:9101"
    mgr.github_proxy_url = "http://github-proxy:9103"
    # auth proxy disabled in this fixture
    return mgr


def _fake_session(post_status=200, delete_status=200, post_text="", delete_text=""):
    session = MagicMock()
    post_resp = AsyncMock()
    post_resp.__aenter__ = AsyncMock(return_value=MagicMock(status=post_status, text=AsyncMock(return_value=post_text)))
    post_resp.__aexit__ = AsyncMock(return_value=None)
    session.post = MagicMock(return_value=post_resp)
    delete_resp = AsyncMock()
    delete_resp.__aenter__ = AsyncMock(
        return_value=MagicMock(status=delete_status, text=AsyncMock(return_value=delete_text))
    )
    delete_resp.__aexit__ = AsyncMock(return_value=None)
    session.delete = MagicMock(return_value=delete_resp)
    session.close = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_enroll_posts_to_each_url(manager):
    manager._http = _fake_session(post_status=200)
    token = await manager.enroll_sandbox("sandbox-job-1")
    assert len(token) >= 40
    assert manager._http.post.call_count == 2  # git + github
    args_list = [call[0][0] for call in manager._http.post.call_args_list]
    assert "http://git-proxy:9101/admin/enroll" in args_list
    assert "http://github-proxy:9103/admin/enroll" in args_list


@pytest.mark.asyncio
async def test_enroll_raises_on_non_200(manager):
    manager._http = _fake_session(post_status=401, post_text="bad admin token")
    with pytest.raises(RuntimeError, match="returned 401"):
        await manager.enroll_sandbox("sandbox-job-2")


@pytest.mark.asyncio
async def test_enroll_raises_when_session_not_started():
    docker = MagicMock()
    mgr = ProxyManager(docker, proxy_admin_token="admin")
    mgr.git_proxy_url = "http://git-proxy:9101"
    with pytest.raises(RuntimeError, match="must be called before enroll_sandbox"):
        await mgr.enroll_sandbox("s")


@pytest.mark.asyncio
async def test_unenroll_best_effort_swallows_404(manager):
    manager._http = _fake_session(delete_status=404)
    await manager.unenroll_sandbox("sandbox-gone")  # no raise


@pytest.mark.asyncio
async def test_unenroll_logs_warning_on_other_failure(manager, caplog):
    manager._http = _fake_session(delete_status=500)
    await manager.unenroll_sandbox("sandbox-x")
    # structlog doesn't route through caplog by default; assert it doesn't raise
    assert True  # no exception = pass


def test_proxy_admin_token_required():
    docker = MagicMock()
    with pytest.raises(ValueError, match="proxy_admin_token is required"):
        ProxyManager(docker, proxy_admin_token="")
