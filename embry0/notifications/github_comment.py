"""GitHub-comment outbound channel.

Posts a single comment per dispatch on the linked GitHub issue with a
numbered question list and a dashboard deep-link. Inbound (parsing
``/answer N: <text>`` from comment webhooks) is Plan B — out of scope here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class GitHubCommentChannel:
    """Conforms to the :class:`NotificationChannel` protocol.

    ``http_client`` must be an ``httpx.AsyncClient`` (or compatible) configured
    with ``base_url`` set to the GitHub API root. The client's pre-set default
    Authorization header is the fallback (single-token deploys); when a
    ``token_resolver`` is provided, its per-repo result wins on a per-request
    basis (httpx per-request headers override client defaults). Construct
    once at app startup and inject everywhere.
    """

    def __init__(
        self,
        http_client: Any,
        dashboard_base_url: str,
        token_resolver: Callable[[str], str] | None = None,
    ) -> None:
        self._client = http_client
        self._dashboard_base = dashboard_base_url.rstrip("/")
        # Per-repo owner token resolution. None => rely on the
        # client's baked default Authorization header (single-token deploys).
        self._token_resolver = token_resolver

    async def dispatch(self, issue: dict[str, Any], questions: list[dict[str, Any]]) -> None:
        gh_number = issue.get("github_number")
        if not isinstance(gh_number, int):
            logger.debug(
                "github_channel_skipped_no_github_number",
                issue_id=issue.get("id"),
            )
            return
        repo = issue.get("repo")
        if not isinstance(repo, str):
            logger.warning("github_channel_invalid_repo", repo=repo, issue_id=issue.get("id"))
            return
        parts = (repo or "").split("/")
        if len(parts) != 2 or not all(parts):
            logger.warning("github_channel_invalid_repo", repo=repo)
            return
        if not questions:
            logger.debug("github_channel_skipped_no_questions", issue_id=issue.get("id"))
            return

        # Question text is agent-authored and rendered as-is in the comment.
        # Markdown chars (`_`, `[]`, etc.) are NOT escaped — agents are trusted
        # to produce sensible question text. If issue titles or untrusted input
        # ever ends up here, add github-flavored-markdown escaping at this layer.
        # Build comment body
        lines = [
            "🤖 **embry0 agent needs input on this issue.**",
            "",
        ]
        for idx, q in enumerate(questions, start=1):
            lines.append(f"{idx}. {q.get('question', '<empty>')}")
        lines.extend(
            [
                "",
                f"Answer at: {self._dashboard_base}/issues/{issue.get('id')}",
                "",
                "_Inbound replies via comments are not yet supported — answer via the dashboard link above._",
            ]
        )
        body = "\n".join(lines)

        url = f"/repos/{repo}/issues/{gh_number}/comments"
        post_kwargs: dict[str, Any] = {"json": {"body": body}}
        if self._token_resolver is not None:
            token = self._token_resolver(repo)
            if token:
                post_kwargs["headers"] = {"Authorization": f"Bearer {token}"}
        try:
            resp = await self._client.post(url, **post_kwargs)
            if resp.status_code >= 400:
                logger.warning(
                    "github_comment_post_failed",
                    repo=repo,
                    issue_number=gh_number,
                    status=resp.status_code,
                )
            else:
                logger.info(
                    "github_comment_posted",
                    repo=repo,
                    issue_number=gh_number,
                    comment_url=resp.json().get("html_url"),
                )
        except Exception as exc:
            logger.warning(
                "github_comment_post_exception",
                repo=repo,
                issue_number=gh_number,
                error=str(exc),
            )
            raise
