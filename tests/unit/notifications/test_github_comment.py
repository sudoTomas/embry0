"""Test GitHub-comment outbound channel."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_github_channel_posts_one_comment_with_numbered_questions():
    from athanor.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(
        return_value=MagicMock(status_code=201, json=lambda: {"id": 999, "html_url": "https://x"})
    )

    channel = GitHubCommentChannel(http_client=fake_client, dashboard_base_url="https://athanor.local")
    issue = {
        "id": "iss-1",
        "repo": "owner/repo",
        "github_number": 42,
        "title": "Test",
    }
    questions = [
        {"question": "[design] Which approach? (a / b)", "input_id": "inp-1", "asking_node": "developer"},
        {"question": "Should I support batch?", "input_id": "inp-2", "asking_node": "developer"},
    ]
    await channel.dispatch(issue, questions)

    fake_client.post.assert_awaited_once()
    args, kwargs = fake_client.post.call_args
    # URL should target the github-proxy
    assert "/repos/owner/repo/issues/42/comments" in args[0] or kwargs.get("url", "").endswith(
        "/repos/owner/repo/issues/42/comments"
    )
    body = kwargs["json"]["body"]
    assert "Athanor agent" in body
    assert "1." in body and "2." in body
    assert "design" in body
    assert "Should I support batch?" in body
    # Dashboard answer link present
    assert "https://athanor.local" in body


@pytest.mark.asyncio
async def test_github_channel_skipped_when_issue_not_github_synced():
    """If github_number is None, the channel is a no-op (no API call)."""
    from athanor.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock()
    channel = GitHubCommentChannel(http_client=fake_client, dashboard_base_url="https://x")
    await channel.dispatch(
        {"id": "iss-1", "repo": "owner/repo", "github_number": None, "title": "T"},
        [{"question": "Q", "input_id": "inp-1"}],
    )
    fake_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_channel_uses_proxy_authorization():
    """Outbound POST routes through the github-proxy with the per-sandbox bearer."""
    from athanor.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(
        return_value=MagicMock(status_code=201, json=lambda: {"id": 1, "html_url": "x"})
    )
    channel = GitHubCommentChannel(http_client=fake_client, dashboard_base_url="https://x")
    issue = {"id": "iss-1", "repo": "o/r", "github_number": 1, "title": "T"}
    await channel.dispatch(issue, [{"question": "Q", "input_id": "inp-1"}])

    # The HTTP client passed in IS the github-proxy-aware client; its
    # base_url + auth headers are configured at construction time. We
    # assert the channel didn't try to bypass it (no separate Bearer
    # header added in the call).
    _, kwargs = fake_client.post.call_args
    # No explicit Authorization header — proxy handles it
    assert "Authorization" not in (kwargs.get("headers") or {})
