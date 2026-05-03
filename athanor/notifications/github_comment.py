"""GitHub-comment outbound channel.

Posts a single comment per dispatch on the linked GitHub issue with a
numbered question list and a dashboard deep-link. Inbound (parsing
``/answer N: <text>`` from comment webhooks) is Plan B — out of scope here.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class GitHubCommentChannel:
    """Conforms to the :class:`NotificationChannel` protocol.

    ``http_client`` must be an ``httpx.AsyncClient`` (or compatible) configured
    with ``base_url`` set to the GitHub API root and the github-proxy's
    Authorization header pre-set on its default headers. Construct once at
    app startup and inject everywhere — the channel itself adds no auth.
    """

    def __init__(self, http_client: Any, dashboard_base_url: str) -> None:
        self._client = http_client
        self._dashboard_base = dashboard_base_url.rstrip("/")

    async def dispatch(
        self, issue: dict[str, Any], questions: list[dict[str, Any]]
    ) -> None:
        gh_number = issue.get("github_number")
        if not isinstance(gh_number, int):
            logger.debug(
                "github_channel_skipped_no_github_number",
                issue_id=issue.get("id"),
            )
            return
        repo = issue.get("repo")
        if not repo or not isinstance(repo, str) or "/" not in repo:
            logger.warning("github_channel_invalid_repo", repo=repo, issue_id=issue.get("id"))
            return

        # Build comment body
        lines = [
            "🤖 **Athanor agent needs input on this issue.**",
            "",
        ]
        for idx, q in enumerate(questions, start=1):
            lines.append(f"{idx}. {q.get('question', '<empty>')}")
        lines.extend(
            [
                "",
                f"Answer at: {self._dashboard_base}/issues/{issue.get('id')}",
                "",
                "_Inbound replies via comments are not yet supported — "
                "answer via the dashboard link above._",
            ]
        )
        body = "\n".join(lines)

        url = f"/repos/{repo}/issues/{gh_number}/comments"
        try:
            resp = await self._client.post(url, json={"body": body})
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
