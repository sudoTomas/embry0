"""GitHub API proxy — list repositories accessible across every configured GitHub token.

The frontend's create-issue dialog uses ``GET /api/v1/github/repos`` to populate
its repository dropdown. Results are fanned out across the global GITHUB_TOKEN
and every per-owner GITHUB_TOKEN__<OWNER>, merged and deduped.
"""

from __future__ import annotations

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from embry0.execution.github_tokens import all_github_tokens

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


async def _fetch_all_repos(default_token: str, *, per_page: int, sort: str) -> list[RepoResponse]:
    """Fetch /user/repos with every configured token; merge, dedup, sort.

    One failing token (e.g. an expired global PAT) must not hide the repos the
    other tokens can see — log and skip it. Raise only when nothing succeeded.
    """
    tokens = all_github_tokens(default_token)
    if not tokens:
        raise HTTPException(status_code=400, detail="No GitHub token configured")

    by_name: dict[str, RepoResponse] = {}
    last_error: httpx.HTTPStatusError | None = None
    succeeded = False
    for token in tokens:
        async with httpx.AsyncClient(headers=_headers(token), timeout=30) as client:
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
                last_error = exc
                continue
        succeeded = True
        for r in resp.json():
            by_name.setdefault(
                r["full_name"],
                RepoResponse(
                    full_name=r["full_name"],
                    description=r.get("description"),
                    private=r["private"],
                    html_url=r["html_url"],
                    default_branch=r.get("default_branch", "main"),
                    language=r.get("language"),
                    open_issues_count=r.get("open_issues_count", 0),
                ),
            )

    if not succeeded:
        status = last_error.response.status_code if last_error else 400
        raise HTTPException(status_code=status, detail=f"GitHub API returned {status}") from last_error

    return sorted(by_name.values(), key=lambda r: r.full_name)


@router.get("/github/repos", response_model=RepoListResponse)
async def list_repos(
    request: Request,
    per_page: int = Query(default=100, le=100, ge=1),
    sort: str = Query(default="updated"),
) -> RepoListResponse:
    """List repositories accessible to ANY configured GitHub token.

    Fans out across the global GITHUB_TOKEN and every GITHUB_TOKEN__<OWNER>,
    merged and deduped — so the create-issue dropdown shows repos from every
    configured owner.
    """
    config = request.app.state.config
    repos = await _fetch_all_repos(config.github_token or "", per_page=per_page, sort=sort)
    return RepoListResponse(repos=repos)
