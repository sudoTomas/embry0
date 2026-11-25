"""Every GitHub consumer outside app.py's own constructions resolves through the registry (spec §3.1).

Each builds its GitHubClient itself, so passing it a resolver proves nothing unless the
request it sends carries the registry token. The env token is empty in every case: after
the documented "remove the env tokens" step, an unwired consumer sends no Authorization.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

REGISTRY_TOKEN = "registry-placeholder-token"


class _Resolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def resolve_forge(self, repo: str, forge: str, *, job_id: str = ""):
        self.calls.append((repo, forge))
        return SimpleNamespace(secret=REGISTRY_TOKEN)


def _recording_http(body):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization", ""))
        return httpx.Response(200, json=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


@pytest.mark.asyncio
async def test_comment_dispatch_fetches_the_pr_with_the_registry_token():
    from embry0.services.comment_dispatch import CommentDispatchService

    # GitHub's pull request read names the default branch, so it is the one call.
    http, seen = _recording_http({"state": "open", "base": {"repo": {"default_branch": "main"}}})
    resolver = _Resolver()
    svc = CommentDispatchService(
        config=SimpleNamespace(github_token=""),
        jobs_repo=MagicMock(),
        agent_sessions_repo=MagicMock(),
        executor=MagicMock(),
        http_client=http,
        credential_resolver=resolver,
    )
    await svc._fetch_pr("acme/widgets", 7)
    assert seen == [f"Bearer {REGISTRY_TOKEN}"], seen
    assert resolver.calls == [("acme/widgets", "github")]


@pytest.mark.asyncio
async def test_watcher_reads_recent_commits_with_the_registry_token():
    from embry0.services.watcher import WatcherService

    http, seen = _recording_http([])
    resolver = _Resolver()
    svc = WatcherService(
        config=SimpleNamespace(github_token="", watcher_repo="acme/widgets"),
        jobs_repo=MagicMock(),
        watcher_runs_repo=MagicMock(),
        integration_repo=MagicMock(),
        executor=MagicMock(),
        filer=None,
        http_client=http,
        credential_resolver=resolver,
    )
    await svc._fetch_recent_commits()
    assert seen == [f"Bearer {REGISTRY_TOKEN}"], seen
    assert resolver.calls == [("acme/widgets", "github")]


def test_github_draft_filer_client_carries_the_resolver():
    from embry0.services.draft_filers import build_filer

    resolver = _Resolver()
    stack = SimpleNamespace(tracker="github", tracker_target={}, repo="acme/widgets")
    filer = build_filer(stack, github_token="", credential_resolver=resolver)
    assert filer._client._credential_resolver is resolver


def test_constructors_default_to_the_env_path():
    """No resolver passed: behaviour is the pre-registry env lookup."""
    from embry0.services.draft_filers import build_filer

    stack = SimpleNamespace(tracker="github", tracker_target={}, repo="acme/widgets")
    assert build_filer(stack, github_token="")._client._credential_resolver is None


@pytest.mark.asyncio
async def test_github_sync_writes_back_with_the_registry_token():
    from embry0.services.github_sync import GitHubSyncService

    resolver = _Resolver()
    svc = GitHubSyncService(github_token="", credential_resolver=resolver)
    assert svc._client._credential_resolver is resolver
    assert await svc._client.token_for("acme/widgets") == REGISTRY_TOKEN
