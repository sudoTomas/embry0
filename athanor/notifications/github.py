"""GitHub issue comment notifications for blocking questions."""

from __future__ import annotations

import httpx
import structlog

logger = structlog.get_logger(__name__)

_GITHUB_API = "https://api.github.com"


async def post_questions_comment(
    github_token: str,
    repo: str,
    issue_number: int,
    questions: list[str],
    asking_node: str,
) -> int | None:
    """Post a single GitHub comment listing all blocking questions.

    Returns the comment_id on success, or None on failure.
    """
    lines = [f"**Legion** — `{asking_node}` has blocking questions:\n"]
    for i, q in enumerate(questions, start=1):
        lines.append(f"{i}. {q}")
    lines.append("\n_Reply in the [Legion Dashboard] or answer here._")
    body = "\n".join(lines)

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{_GITHUB_API}/repos/{repo}/issues/{issue_number}/comments"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json={"body": body}, headers=headers)

    if resp.status_code in (200, 201):
        comment_id: int = resp.json()["id"]
        logger.info(
            "github_questions_comment_posted",
            repo=repo,
            issue_number=issue_number,
            comment_id=comment_id,
        )
        return comment_id

    logger.error(
        "github_questions_comment_failed",
        repo=repo,
        issue_number=issue_number,
        status=resp.status_code,
        body=resp.text[:200],
    )
    return None
