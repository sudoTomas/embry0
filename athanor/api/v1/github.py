"""GitHub API proxy — list the repositories accessible to the configured GITHUB_TOKEN.

The frontend's create-issue dialog uses ``GET /api/v1/github/repos`` to populate
its repository dropdown. The token's permissions determine which repos appear.
"""

from __future__ import annotations

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

logger = structlog.get_logger(__name__)
router = APIRouter()

_BASE = "https://api.github.com"


class RepoResponse(BaseModel):
    full_name: str
    description: str | None = None
    private: bool
    html_url: str
    default_branch: str
    language: str | None = None
    open_issues_count: int


class RepoListResponse(BaseModel):
    repos: list[RepoResponse]


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


@router.get("/github/repos", response_model=RepoListResponse)
async def list_repos(
    request: Request,
    per_page: int = Query(default=100, le=100, ge=1),
    sort: str = Query(default="updated"),
) -> RepoListResponse:
    """List repositories the configured GITHUB_TOKEN has access to.

    Returns repos the token can read across owner, collaborator, and
    organization-member contexts. Sorted by the chosen GitHub field
    (default: most recently updated).
    """
    config = request.app.state.config
    if not config.github_token:
        raise HTTPException(status_code=400, detail="GITHUB_TOKEN not configured")

    async with httpx.AsyncClient(headers=_headers(config.github_token), timeout=30) as client:
        try:
            resp = await client.get(
                f"{_BASE}/user/repos",
                params={
                    "per_page": per_page,
                    "sort": sort,
                    "affiliation": "owner,collaborator,organization_member",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("github_repos_fetch_failed", status=exc.response.status_code)
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"GitHub API returned {exc.response.status_code}",
            ) from exc

    repos = [
        RepoResponse(
            full_name=r["full_name"],
            description=r.get("description"),
            private=r["private"],
            html_url=r["html_url"],
            default_branch=r.get("default_branch", "main"),
            language=r.get("language"),
            open_issues_count=r.get("open_issues_count", 0),
        )
        for r in resp.json()
    ]

    return RepoListResponse(repos=repos)
