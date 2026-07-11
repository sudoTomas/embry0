"""Test GitHub-comment outbound channel."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_github_channel_posts_one_comment_with_numbered_questions():
    from embry0.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(
        return_value=MagicMock(status_code=201, json=lambda: {"id": 999, "html_url": "https://x"})
    )

    channel = GitHubCommentChannel(http_client=fake_client, dashboard_base_url="https://embry0.local")
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
    assert "embry0 agent" in body
    assert "1." in body and "2." in body
    assert "design" in body
    assert "Should I support batch?" in body
    # Dashboard answer link present
    assert "https://embry0.local" in body


@pytest.mark.asyncio
async def test_github_channel_skipped_when_issue_not_github_synced():
    """If github_number is None, the channel is a no-op (no API call)."""
    from embry0.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock()
    channel = GitHubCommentChannel(http_client=fake_client, dashboard_base_url="https://x")
    await channel.dispatch(
        {"id": "iss-1", "repo": "owner/repo", "github_number": None, "title": "T"},
        [{"question": "Q", "input_id": "inp-1"}],
    )
    fake_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_channel_does_not_add_per_call_auth_header():
    """Auth lives on the injected http_client's default headers (set at app
    lifespan time with the orchestrator's GITHUB_TOKEN). The channel itself
    must not add another Authorization header per-call — that would either
    duplicate or override the client default."""
    from embry0.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=MagicMock(status_code=201, json=lambda: {"id": 1, "html_url": "x"}))
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


@pytest.mark.asyncio
async def test_dispatch_uses_token_resolver_per_repo():
    """When a token_resolver is provided, dispatch adds a per-request
    Authorization header derived from it (per-repo owner tokens)."""
    from embry0.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=MagicMock(status_code=201, json=lambda: {"id": 1, "html_url": "x"}))
    channel = GitHubCommentChannel(
        http_client=fake_client,
        dashboard_base_url="http://dash",
        token_resolver=lambda repo: f"tok-for-{repo.split('/', 1)[0]}",
    )
    issue = {"id": "i1", "repo": "acme-corp/widgets", "github_number": 7}
    await channel.dispatch(issue, [{"question": "Q?"}])

    _, kwargs = fake_client.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer tok-for-acme-corp"


@pytest.mark.asyncio
async def test_dispatch_without_resolver_sends_no_per_request_auth():
    """Without a token_resolver, dispatch falls back to today's behavior:
    no per-request Authorization header (client-level default header wins)."""
    from embry0.notifications.github_comment import GitHubCommentChannel

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=MagicMock(status_code=201, json=lambda: {"id": 1, "html_url": "x"}))
    channel = GitHubCommentChannel(http_client=fake_client, dashboard_base_url="http://dash")
    issue = {"id": "i1", "repo": "acme-corp/widgets", "github_number": 7}
    await channel.dispatch(issue, [{"question": "Q?"}])

    _, kwargs = fake_client.post.call_args
    assert "headers" not in kwargs or kwargs.get("headers") is None
