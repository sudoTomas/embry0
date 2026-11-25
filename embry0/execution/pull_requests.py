"""A job's pull request on its forge: read it, comment on it, close it (#181).

The actions on a job's pull request (send back, reject and close) and the guild's reads of it speak to the forge
through :class:`PullRequests`, so no route builds a forge's paths. Two implementations, each over that forge's own
client: :class:`GitHubPullRequests` (``/repos/<repo>/pulls/<n>``) and :class:`GitLabPullRequests`
(``/projects/<repo>/merge_requests/<iid>``). Which one a repository gets is its forge's:
:func:`pull_requests_for`.

Each answer is what the forge said, read into one shape: a pull request that cannot be read is None, and a write
says whether the forge took it. Nothing here raises for a forge's refusal or a failed request; a client that cannot
be used at all (a chosen token asked for another owner's repository) still does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

import structlog

from embry0.execution.forge_client import ForgeWrite, forge_write
from embry0.execution.gitlab_client import encode_project
from embry0.execution.gitlab_notes import without_quick_actions

logger = structlog.get_logger(__name__)

State = Literal["open", "closed", "merged"]


@dataclass(frozen=True)
class PullRequest:
    """A pull or merge request as its forge reads it.

    - number: GitHub's number, GitLab's iid;
    - state: open, closed, or merged;
    - head_ref: the branch its changes are on (GitLab's source branch);
    - url: its address on the forge's web pages;
    - from_fork: its changes come from another repository than its own (GitLab: another project), or one the
      forge no longer names: ``head_ref`` is then a branch of that repository, not of the pull request's own;
    - files, additions, deletions, commits: what it changes, None where the forge's read does not say;
    - default_branch: its repository's default branch, when the read gives it (GitHub's does; GitLab's merge
      request read does not, and :meth:`PullRequests.default_branch` reads it).
    """

    number: int
    state: State
    head_ref: str
    url: str
    from_fork: bool
    title: str = ""
    files: int | None = None
    additions: int | None = None
    deletions: int | None = None
    commits: int | None = None
    default_branch: str | None = None


class PullRequests(Protocol):
    """A forge's pull requests, for the actions on a job's pull request."""

    forge: str

    async def read(self, repo: str, number: int) -> PullRequest | None:
        """The pull request, or None when it is missing or cannot be read."""

    async def comment(self, repo: str, number: int, body: str) -> ForgeWrite:
        """Post ``body`` as a comment on it."""

    async def close(self, repo: str, number: int) -> ForgeWrite:
        """Close it. Safe to send again: closing a closed one leaves it closed."""

    async def default_branch(self, repo: str) -> str | None:
        """The repository's default branch, or None when it cannot be read."""


def _count(value: Any) -> int | None:
    """A count the forge gave: an int, or GitLab's ``changes_count`` string (``"12"``, or ``"1000+"`` past its
    limit, read as the limit); None for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        digits = value.rstrip("+")
        return int(digits) if digits.isdigit() else None
    return None


async def _default_branch(client: Any, path: str, repo: str, forge: str) -> str | None:
    """``default_branch`` of the repository (GitHub) or project (GitLab) at ``path``; None when it cannot be read."""
    resp = await client.get(path, repo=repo)
    body = resp.json() if resp.status_code == 200 else None
    branch = body.get("default_branch") if isinstance(body, dict) else None
    if not isinstance(branch, str) or not branch:
        logger.info("default_branch_unread", forge=forge, repo=repo, status=resp.status_code)
        return None
    return branch


class GitHubPullRequests:
    """GitHub's pull requests, over a :class:`~embry0.execution.github_client.GitHubClient`."""

    forge = "github"

    def __init__(self, client: Any) -> None:
        self._client = client

    async def read(self, repo: str, number: int) -> PullRequest | None:
        resp = await self._client.get(f"/repos/{repo}/pulls/{number}", repo=repo)
        if resp.status_code != 200:
            logger.info("pull_request_unread", forge=self.forge, repo=repo, number=number, status=resp.status_code)
            return None
        body = resp.json()
        if not isinstance(body, dict):
            return None
        state: State = "merged" if body.get("merged") else ("open" if body.get("state") == "open" else "closed")
        head, base = body.get("head") or {}, body.get("base") or {}
        # A fork: any head repository other than the base, a deleted one (head.repo null) included, as the
        # reviewer reads it (pr_review.triggers.pr_from_github).
        head_repo = str((head.get("repo") or {}).get("full_name") or "")
        base_repo = str((base.get("repo") or {}).get("full_name") or "") or repo
        default_branch = str((base.get("repo") or {}).get("default_branch") or "") or None
        return PullRequest(
            number=number,
            state=state,
            head_ref=str(head.get("ref") or ""),
            url=str(body.get("html_url") or ""),
            from_fork=not head_repo or head_repo.lower() != base_repo.lower(),
            title=str(body.get("title") or ""),
            default_branch=default_branch,
            files=_count(body.get("changed_files")),
            additions=_count(body.get("additions")),
            deletions=_count(body.get("deletions")),
            commits=_count(body.get("commits")),
        )

    async def comment(self, repo: str, number: int, body: str) -> ForgeWrite:
        resp = await self._client.post(f"/repos/{repo}/issues/{number}/comments", repo=repo, json={"body": body})
        return forge_write(resp)

    async def close(self, repo: str, number: int) -> ForgeWrite:
        resp = await self._client.patch(
            f"/repos/{repo}/pulls/{number}", repo=repo, json={"state": "closed"}, retry_transient=True
        )
        return forge_write(resp)

    async def default_branch(self, repo: str) -> str | None:
        return await _default_branch(self._client, f"/repos/{repo}", repo, self.forge)


class GitLabPullRequests:
    """GitLab's merge requests, over a :class:`~embry0.execution.gitlab_client.GitLabClient`."""

    forge = "gitlab"

    def __init__(self, client: Any) -> None:
        self._client = client

    def _path(self, repo: str, iid: int, suffix: str = "") -> str:
        return f"/projects/{encode_project(repo)}/merge_requests/{iid}{suffix}"

    async def read(self, repo: str, number: int) -> PullRequest | None:
        resp = await self._client.get(self._path(repo, number), repo=repo)
        if resp.status_code != 200:
            logger.info("pull_request_unread", forge=self.forge, repo=repo, number=number, status=resp.status_code)
            return None
        body = resp.json()
        if not isinstance(body, dict):
            return None
        # opened, closed, merged, or locked: a locked one is on its way to merged or closed, and takes no follow-up.
        raw = str(body.get("state") or "")
        state: State = "merged" if raw == "merged" else ("open" if raw == "opened" else "closed")
        # A fork: a source project other than the target, or either one missing, as the reviewer reads it
        # (pr_review.triggers.pr_from_gitlab).
        source, target = body.get("source_project_id"), body.get("target_project_id")
        return PullRequest(
            number=number,
            state=state,
            head_ref=str(body.get("source_branch") or ""),
            url=str(body.get("web_url") or ""),
            from_fork=source is None or target is None or source != target,
            title=str(body.get("title") or ""),
            files=_count(body.get("changes_count")),
        )

    async def comment(self, repo: str, number: int, body: str) -> ForgeWrite:
        """A note: with no line GitLab would run as a quick action (``embry0.execution.gitlab_notes``)."""
        resp = await self._client.post(
            self._path(repo, number, "/notes"), repo=repo, json={"body": without_quick_actions(body)}
        )
        return forge_write(resp)

    async def close(self, repo: str, number: int) -> ForgeWrite:
        resp = await self._client.put(
            self._path(repo, number), repo=repo, json={"state_event": "close"}, retry_transient=True
        )
        return forge_write(resp)

    async def default_branch(self, repo: str) -> str | None:
        return await _default_branch(self._client, f"/projects/{encode_project(repo)}", repo, self.forge)


def pull_requests_for(forge: str, client: Any) -> PullRequests:
    """The pull requests of ``forge`` (``github`` or ``gitlab``) over ``client``, that forge's own client."""
    if forge == "gitlab":
        return GitLabPullRequests(client)
    if forge == "github":
        return GitHubPullRequests(client)
    raise ValueError(f"no pull requests for forge {forge!r}")
