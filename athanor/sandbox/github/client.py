"""GitHub API client inside the sandbox — all requests go through the proxy."""

import httpx
import structlog

from athanor.sandbox.events import EventType, emit_event

logger = structlog.get_logger(__name__)


class SandboxGitHubClient:
    """GitHub API client that routes through the GitHub API proxy."""

    def __init__(self, proxy_url: str, repo: str) -> None:
        self._proxy_url = proxy_url.rstrip("/")
        self._repo = repo
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "SandboxGitHubClient":
        self._client = httpx.AsyncClient(
            base_url=self._proxy_url,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client:
            await self._client.aclose()

    async def create_pr(self, branch: str, title: str, body: str, base: str = "main") -> str:
        """Create a pull request. Returns the PR URL."""
        assert self._client is not None
        resp = await self._client.post(
            f"/repos/{self._repo}/pulls",
            json={"head": branch, "base": base, "title": title, "body": body},
        )
        resp.raise_for_status()
        pr_url = resp.json()["html_url"]
        emit_event(EventType.GITHUB_API, op="create_pr", pr_url=pr_url)
        return pr_url

    async def post_comment(self, issue_number: int, body: str) -> None:
        """Post a comment on an issue or PR."""
        assert self._client is not None
        resp = await self._client.post(
            f"/repos/{self._repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        resp.raise_for_status()
        emit_event(EventType.GITHUB_API, op="post_comment", issue_number=issue_number)

    async def get_issue(self, issue_number: int) -> dict:
        """Fetch issue details."""
        assert self._client is not None
        resp = await self._client.get(f"/repos/{self._repo}/issues/{issue_number}")
        resp.raise_for_status()
        return resp.json()
