"""One HTTP client for every GitHub REST call (#158).

Before this module, each caller re-derived the whole stack: eight copies of
the base URL, nine near-identical header blocks (two of which forgot
``X-GitHub-Api-Version``), five copies of the
``client = self._http or httpx.AsyncClient(...)`` lifecycle idiom, timeouts of
10, 15, and 30 seconds picked per call site, three mutually incompatible error
policies, and pagination implemented exactly once, ad hoc, in the QA sticky
comment. Nothing anywhere retried, backed off, or looked at a rate-limit
header.

Three call sites also took a flat ``github_token`` and never consulted
:func:`embry0.execution.github_tokens.resolve_for_repo`, so per-owner
``GITHUB_TOKEN__<OWNER>`` overrides silently did not apply to them. Routing
every request through one place fixes that class of bug by construction.

Retry, backoff, lifecycle and pagination now live in
:class:`embry0.execution.forge_client.ForgeClient`, shared with GitLab (#164).
What remains here is what is genuinely GitHub-specific: the API base, the
header trio, and per-owner token resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from embry0.execution.forge_client import (
    DEFAULT_TIMEOUT,
    MAX_BACKOFF_SECONDS,
    RETRY_STATUSES,
    ForgeClient,
    ForgeWrite,
    forge_write,
    retry_after_seconds,
)
from embry0.execution.github_tokens import resolve_for_repo

logger = structlog.get_logger(__name__)

GITHUB_API = "https://api.github.com"
API_VERSION = "2022-11-28"
DEFAULT_ACCEPT = "application/vnd.github+json"

# Re-exported so existing importers keep working.
__all__ = [
    "API_VERSION",
    "DEFAULT_ACCEPT",
    "DEFAULT_TIMEOUT",
    "GITHUB_API",
    "MAX_BACKOFF_SECONDS",
    "RETRY_STATUSES",
    "GitHubClient",
    "MergeResult",
    "retry_after_seconds",
]


@dataclass(frozen=True)
class MergeResult:
    """What ``PUT /pulls/{n}/merge`` answered."""

    status_code: int
    merged: bool
    sha: str = ""
    message: str = ""


class GitHubClient(ForgeClient):
    """Thin, retrying wrapper over the GitHub REST API."""

    forge = "github"

    def __init__(self, *, base_url: str = GITHUB_API, **kwargs: object) -> None:
        super().__init__(base_url=base_url, **kwargs)  # type: ignore[arg-type]

    def headers(
        self, repo: str | None = None, *, accept: str = DEFAULT_ACCEPT, token: str | None = None, **_: object
    ) -> dict[str, str]:
        """Standard headers. ``token`` from the registry wins; None runs the env lookup."""
        built = {"Accept": accept, "X-GitHub-Api-Version": API_VERSION}
        if token is None:
            token = resolve_for_repo(repo, self._default_token)
        if token:
            built["Authorization"] = f"Bearer {token}"
        return built

    async def get_repo(self, repo: str) -> dict[str, object] | None:
        """Repository record, or None when it is missing or not visible.

        The raw read, with no fallback of its own — the GitHub counterpart of
        ``GitLabClient.get_project``. A caller that must tell "the repo says
        main" apart from "the call failed" (``services.base_branch.
        _forge_default``, Spec 1A §3.8) cannot use
        :meth:`get_repo_default_branch`, whose whole contract is to answer
        ``"main"`` either way.
        """
        resp = await self.get(f"/repos/{repo}", repo=repo)
        if resp.status_code != 200:
            return None
        body = resp.json()
        return body if isinstance(body, dict) else None

    async def get_repo_default_branch(self, repo: str) -> str:
        """The repo's default branch, falling back to ``main``.

        The GitLab counterpart is ``services.gitlab_mrs.default_branch_for``;
        both exist because targeting a branch that does not exist fails the
        MR/PR create with a message that does not obviously say so. Never
        raises — a 404, a rate limit or a network blip yields the historical
        assumption rather than killing the caller.
        """
        try:
            record = await self.get_repo(repo)
        except Exception:
            return "main"
        branch = (record or {}).get("default_branch")
        return str(branch) if isinstance(branch, str) and branch else "main"

    # -- pull requests (the merge-approval loop, #319 phase 2) -------------

    async def get_pull(self, repo: str, number: int) -> dict[str, Any] | None:
        """The PR record, or None when it is missing, not visible or the call failed."""
        resp = await self.get(f"/repos/{repo}/pulls/{number}", repo=repo)
        if resp.status_code != 200:
            return None
        body = resp.json()
        return body if isinstance(body, dict) else None

    async def list_pull_files(self, repo: str, number: int) -> list[str]:
        """Paths the PR changes. GitHub caps this listing at 3000 files, and
        ``paginate`` stops quietly on an error page, so a caller that must know
        the list is whole compares its length to the PR's ``changed_files``."""
        items = await self.paginate(f"/repos/{repo}/pulls/{number}/files", repo=repo, max_pages=30)
        return [str(f["filename"]) for f in items if isinstance(f, dict) and f.get("filename")]

    async def list_pull_file_patches(self, repo: str, number: int) -> list[dict[str, Any]]:
        """``pulls/{n}/files`` items with their ``patch`` (#30).

        Raises ``httpx.HTTPStatusError`` on an error page: a reviewer that
        placed comments against half the files would place them wrongly.
        GitHub omits ``patch`` for binary and very large files.
        """
        items = await self.paginate(f"/repos/{repo}/pulls/{number}/files", repo=repo, max_pages=30, strict=True)
        return [f for f in items if isinstance(f, dict)]

    async def create_pull_review(
        self, repo: str, number: int, *, commit_id: str | None, body: str, comments: list[dict[str, Any]]
    ) -> ForgeWrite:
        """Post one review with ``event: COMMENT`` (#30) — advisory: never APPROVE or REQUEST_CHANGES.

        ``commit_id`` pins the inline comments to the reviewed commit; None
        lets GitHub use the pull request's head (a body-only review needs no pin).
        """
        payload: dict[str, Any] = {"body": body, "event": "COMMENT", "comments": comments}
        if commit_id:
            payload = {"commit_id": commit_id, **payload}
        # Sent once: a review GitHub accepted and answered 502 must not be posted twice.
        resp = await self.post(f"/repos/{repo}/pulls/{number}/reviews", repo=repo, json=payload, retry_transient=False)
        return forge_write(resp)

    async def get_check_runs(self, repo: str, sha: str) -> dict[str, Any] | None:
        """One page (100) of check runs on ``sha`` as ``{total_count, check_runs}``,
        or None when the call failed. The body is an object, not a list, so
        ``paginate`` does not apply; a caller seeing ``total_count`` above the
        runs returned knows it did not see them all."""
        resp = await self.get(f"/repos/{repo}/commits/{sha}/check-runs", repo=repo, params={"per_page": 100})
        if resp.status_code != 200:
            return None
        body = resp.json()
        return body if isinstance(body, dict) else None

    async def get_combined_status(self, repo: str, sha: str) -> dict[str, Any] | None:
        """Legacy commit statuses on ``sha``, or None when the call failed.

        A commit with NO statuses reports ``state: "pending"``; read
        ``total_count`` before trusting ``state``."""
        resp = await self.get(f"/repos/{repo}/commits/{sha}/status", repo=repo, params={"per_page": 100})
        if resp.status_code != 200:
            return None
        body = resp.json()
        return body if isinstance(body, dict) else None

    async def merge_pull(
        self,
        repo: str,
        number: int,
        *,
        sha: str,
        method: str = "squash",
        commit_title: str | None = None,
    ) -> MergeResult:
        """Merge the PR only if its head is still ``sha``.

        GitHub answers 409 when the head moved since ``sha`` — the pin that
        stops a push after the approval from riding along — and 405 when the
        PR is not mergeable. Never raises on a non-2xx; the caller decides.
        """
        if not sha:
            raise ValueError("merge_pull needs the head sha the approval was given for")
        payload: dict[str, Any] = {"sha": sha, "merge_method": method}
        if commit_title:
            payload["commit_title"] = commit_title
        resp = await self.put(f"/repos/{repo}/pulls/{number}/merge", repo=repo, json=payload)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        return MergeResult(
            status_code=resp.status_code,
            merged=resp.status_code == 200 and bool(body.get("merged")),
            sha=str(body.get("sha") or ""),
            message=str(body.get("message") or ""),
        )

    # -- issues (a watched stack whose tracker is GitHub Issues, #378) ------

    async def create_issue(self, repo: str, *, title: str, body: str) -> dict[str, Any] | None:
        """Open an issue. NEVER labelled: the trigger label is the human's
        acceptance step, so a filer that could label would dispatch the
        pipeline the moment it filed (see trackers/filing.py).

        Returns the created issue, or None when GitHub refused.
        """
        resp = await self.post(f"/repos/{repo}/issues", repo=repo, json={"title": title, "body": body})
        if resp.status_code not in (200, 201):
            logger.warning("github_issue_create_failed", repo=repo, status=resp.status_code, detail=resp.text[:200])
            return None
        created = resp.json()
        return created if isinstance(created, dict) else None

    async def list_issues(self, repo: str, *, state: str = "open", limit: int = 30) -> list[dict[str, Any]]:
        """Issues, most recently updated first. Raises nothing, but returns
        what it got — the caller decides whether a short list is fatal.

        The listing includes pull requests (every PR is an issue); callers
        that want issues only drop entries carrying ``pull_request``.
        """
        resp = await self.get(
            f"/repos/{repo}/issues",
            repo=repo,
            params={"state": state, "per_page": min(max(limit, 1), 100), "sort": "updated", "direction": "desc"},
        )
        if resp.status_code != 200:
            logger.warning("github_issue_list_failed", repo=repo, status=resp.status_code, detail=resp.text[:200])
            return []
        body = resp.json()
        return [i for i in body if isinstance(i, dict)] if isinstance(body, list) else []

    async def get_issue(self, repo: str, number: int) -> dict[str, Any] | None:
        """One issue, or None when it is gone or unreadable — which the
        watcher reads as "missing" and frees its cap slot for."""
        resp = await self.get(f"/repos/{repo}/issues/{number}", repo=repo)
        if resp.status_code != 200:
            return None
        issue = resp.json()
        return issue if isinstance(issue, dict) else None
