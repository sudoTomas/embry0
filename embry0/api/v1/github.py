"""GitHub API proxy — list repositories accessible across every configured GitHub token.

The frontend's create-issue dialog uses ``GET /api/v1/github/repos`` to populate
its repository dropdown. Results are fanned out across the global GITHUB_TOKEN
and every per-owner GITHUB_TOKEN__<OWNER>, merged and deduped.

``GET /api/v1/github/discoverable-repos`` (RAV-1301) is the registration-side
view: registry credentials for github, then the env tokens, minus repos already
registered. Each row names the granting credential (id and label) or, for an
env token, the env-key name — never the value — plus a best-effort push flag.
Because fine-grained PATs are single-owner and repo-scoped, the union across
accounts is the honest "what embry0 can touch" list.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from embry0.api.forge_listings import PastDeadline, nobody_answered, read_each
from embry0.api.repo_allowlist import key_covers, restricted_key_name
from embry0.api.v1.credentials import discovery_entries
from embry0.execution.github_client import GitHubClient
from embry0.services.credentials.discovery import ForgeCredentialEntry, forge_credential_entries

logger = structlog.get_logger(__name__)
router = APIRouter()


# Hard per-token page cap for the paginated /user/repos walk. A broad classic
# PAT (org admin) can see thousands of repos; 5 pages x 100 = 500 per token is
# plenty for a registration dropdown, and truncation is surfaced honestly via
# ``truncated_env_keys`` rather than silently dropped.
MAX_PAGES_PER_TOKEN = 5


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


class DiscoverableRepo(RepoResponse):
    token_env_key: str = Field(
        description=(
            "Name of the env key whose token grants access (e.g. "
            "GITHUB_TOKEN__ACME). Empty when the row came from the registry. "
            "Names only — token values never leave the orchestrator."
        )
    )
    credential_id: str | None = Field(
        default=None,
        description="Registry credential that granted this repo. Null for an env token.",
    )
    credential_label: str = Field(
        default="",
        description="Registry label, or the env key when the row came from the environment.",
    )
    push: bool | None = Field(
        default=None,
        description=(
            "Advisory only: GitHub's permissions.push reflects the token's "
            "USER, not what a fine-grained PAT actually grants — test real "
            "access with a git-refs write, never this field. None when the "
            "API omitted permissions."
        ),
    )


class DiscoverableReposResponse(BaseModel):
    repos: list[DiscoverableRepo]
    truncated_env_keys: list[str]
    cached: bool
    failed: list[str] = Field(
        default_factory=list,
        description="Labels of credentials whose listing raised. Never a token.",
    )


def _headers(token: str) -> dict[str, str]:
    """Standard GitHub headers. Delegates so there is one definition (#158)."""
    return GitHubClient(default_token=token, owner_env_tokens=False).headers()


def _to_repo_response(r: dict[str, Any]) -> RepoResponse:
    return RepoResponse(
        full_name=r["full_name"],
        description=r.get("description"),
        private=r["private"],
        html_url=r["html_url"],
        default_branch=r.get("default_branch", "main"),
        language=r.get("language"),
        open_issues_count=r.get("open_issues_count", 0),
    )


async def _fetch_repos_for_token(
    env_key: str, token: str, *, per_page: int, sort: str
) -> tuple[list[dict[str, Any]], bool]:
    """Paginated /user/repos walk for one token via the Link header.

    Returns the raw repo dicts and whether MAX_PAGES_PER_TOKEN truncated a
    listing that still had a ``next`` page. Query params ride only the first
    request — GitHub's ``next`` URL already carries them.
    """
    raw: list[dict[str, Any]] = []
    truncated = False
    async with httpx.AsyncClient(headers=_headers(token), timeout=30) as client:
        url = GitHubClient().url("/user/repos")
        params: dict[str, Any] | None = {
            "per_page": per_page,
            "sort": sort,
            "affiliation": "owner,collaborator,organization_member",
        }
        pages = 0
        while True:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            raw.extend(resp.json())
            pages += 1
            next_url = resp.links.get("next", {}).get("url")
            if not next_url:
                break
            if pages >= MAX_PAGES_PER_TOKEN:
                truncated = True
                logger.warning("github_repos_listing_truncated", env_key=env_key, pages=pages)
                break
            url, params = next_url, None
    return raw, truncated


async def _fetch_all_repo_entries(
    entries: list[ForgeCredentialEntry], *, per_page: int, sort: str
) -> tuple[dict[str, tuple[ForgeCredentialEntry, dict[str, Any]]], list[str], list[str], bool]:
    """Fan /user/repos out across every credential entry.

    Returns ``full_name -> (granting entry, raw repo dict)`` — first entry
    wins on duplicates — the keys whose listings were truncated, the labels
    whose listings raised, and whether the answer is complete: false when an
    account is missing for a reason the next read may not have (it could not
    be reached, or ran past a deadline), so the answer is not cached.

    One failing credential (e.g. an expired PAT) must not hide the repos the
    others can see — log it, record its label, and continue. That holds for a
    listing GitHub refused (an HTTP status), one that never got an answer (a
    timeout, a refused connection) and one not done by the listing's deadline:
    the accounts are read concurrently, within one deadline
    (:func:`embry0.api.forge_listings.read_each`, #340). Raise only when
    nothing succeeded, 502 either way, its detail counting what GitHub did
    (:func:`embry0.api.forge_listings.nobody_answered`): GitHub refusing
    every account is not the caller's credentials failing, and a 401 would
    read as a bad embry0 key (#568). An empty entry list is "nothing is
    configured".
    """
    if not entries:
        raise HTTPException(status_code=400, detail="No GitHub token configured")

    by_name: dict[str, tuple[ForgeCredentialEntry, dict[str, Any]]] = {}
    truncated_env_keys: list[str] = []
    failed: list[str] = []
    refused: list[int] = []
    unreachable = 0
    cause: Exception | None = None
    succeeded = False

    async def read(entry: ForgeCredentialEntry) -> tuple[list[dict[str, Any]], bool]:
        return await _fetch_repos_for_token(entry.key, entry.token, per_page=per_page, sort=sort)

    # In the order of the entries, so the first entry still wins a repository two can see.
    for entry, result in zip(entries, await read_each(entries, read), strict=True):
        if isinstance(result, (httpx.HTTPStatusError, httpx.RequestError, PastDeadline)):
            status = result.response.status_code if isinstance(result, httpx.HTTPStatusError) else None
            # The exception's class, not its text: the text can carry the request's URL.
            logger.warning(
                "github_repos_fetch_failed",
                status=status,
                error=type(result).__name__,
                env_key=entry.key,
                label=entry.label,
            )
            failed.append(entry.label)
            if status is not None:
                refused.append(status)
            else:
                unreachable += 1
            cause = result
            continue
        if isinstance(result, BaseException):
            raise result
        raw, truncated = result
        succeeded = True
        if truncated:
            truncated_env_keys.append(entry.key)
        shown = entry.without_token()
        for r in raw:
            by_name.setdefault(r["full_name"], (shown, r))

    if not succeeded:
        raise HTTPException(status_code=502, detail=nobody_answered("GitHub", refused, unreachable)) from cause

    return by_name, truncated_env_keys, failed, unreachable == 0


async def _env_github_entries(default_token: str) -> list[ForgeCredentialEntry]:
    """Env tokens only. ``GET /github/repos`` has no registry view; discovery does."""
    return await forge_credential_entries(None, None, SimpleNamespace(github_token=default_token), "github")


async def _fetch_all_repos(default_token: str, *, per_page: int, sort: str) -> list[RepoResponse]:
    """Fetch /user/repos with every configured env token; merge, dedup, sort."""
    by_name, _, _, _ = await _fetch_all_repo_entries(
        await _env_github_entries(default_token), per_page=per_page, sort=sort
    )
    return sorted(
        (_to_repo_response(raw) for _, raw in by_name.values()),
        key=lambda r: r.full_name,
    )


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

    A key with a repository allowlist lists with its own credentials only, and
    only the repositories its list covers (``embry0.api.v1.credentials.discovery_entries``).
    """
    if restricted_key_name(request) is not None:
        by_name, _, _, _ = await _fetch_all_repo_entries(
            await discovery_entries(request, "github"), per_page=per_page, sort=sort
        )
        own = (_to_repo_response(raw) for name, (_, raw) in by_name.items() if key_covers(request, name))
        return RepoListResponse(repos=sorted(own, key=lambda r: r.full_name))
    config = request.app.state.config
    repos = await _fetch_all_repos(config.github_token or "", per_page=per_page, sort=sort)
    return RepoListResponse(repos=repos)


# Single-slot fan-out cache for the discovery endpoint. Caches ONLY the GitHub
# fan-out — the registered subtraction re-reads Postgres every call so a
# just-registered repo drops out of the dropdown immediately. 300s matches the
# frontend's staleTime and bounds the cost of a capped fan-out (up to
# MAX_PAGES_PER_TOKEN x N accounts). ``refresh=true`` bypasses it. An empty
# result is never cached, so a transient all-accounts failure doesn't stick.
_DISCOVERY_CACHE: dict[str, Any] = {"at": 0.0, "by_name": {}, "truncated": [], "failed": []}
_DISCOVERY_CACHE_TTL_SECONDS = 300.0


@router.get("/github/discoverable-repos", response_model=DiscoverableReposResponse)
async def list_discoverable_repos(
    request: Request,
    refresh: bool = Query(default=False),
) -> DiscoverableReposResponse:
    """List repos the configured accounts can see, minus already-registered ones.

    Feeds the central Add-repo dropdown. A registry row names its
    credential; an env row names the env key. ``failed`` is labels whose
    listing raised. ``refresh=true`` bypasses the fan-out cache.

    A key with a repository allowlist lists with its own credentials only,
    never the environment's tokens nor a credential it may not use, and sees
    only the repositories its list covers; its listing is read for it alone,
    never from or into the shared cache (``cached`` false). With no
    credential of its own it is answered as a deployment with no token
    (``embry0.api.v1.credentials.discovery_entries``).
    """
    if restricted_key_name(request) is not None:
        by_name, truncated, failed, _complete = await _fetch_all_repo_entries(
            await discovery_entries(request, "github"), per_page=100, sort="updated"
        )
        listing: dict[str, Any] = {"by_name": by_name, "truncated": truncated, "failed": failed}
        cached = False
    else:
        now = time.monotonic()
        cached = (
            not refresh
            and bool(_DISCOVERY_CACHE["by_name"])
            and now - _DISCOVERY_CACHE["at"] < _DISCOVERY_CACHE_TTL_SECONDS
        )
        if not cached:
            entries = await discovery_entries(request, "github")
            by_name, truncated, failed, complete = await _fetch_all_repo_entries(entries, per_page=100, sort="updated")
            # An answer missing an account that could not be reached, or ran past a deadline, is served once and not
            # kept: the next read asks every account again.
            _DISCOVERY_CACHE["at"] = time.monotonic() if complete else 0.0
            _DISCOVERY_CACHE["by_name"] = by_name
            _DISCOVERY_CACHE["truncated"] = truncated
            _DISCOVERY_CACHE["failed"] = failed
        listing = _DISCOVERY_CACHE

    # include_inactive: a deactivated repo is still registered — offering it
    # for (no-op) re-registration would only confuse the operator.
    registered = {row["repo"] for row in await request.app.state.repos_repo.list_all(include_inactive=True)}

    repos: list[DiscoverableRepo] = []
    for full_name in sorted(listing["by_name"]):
        if full_name in registered or not key_covers(request, full_name):
            continue
        entry, raw = listing["by_name"][full_name]
        permissions = raw.get("permissions") or {}
        repos.append(
            DiscoverableRepo(
                **_to_repo_response(raw).model_dump(),
                token_env_key=entry.key if entry.source == "env" else "",
                credential_id=entry.credential_id,
                credential_label=entry.label,
                push=permissions.get("push"),
            )
        )

    truncated_env_keys = list(listing["truncated"])
    failed = list(listing["failed"])
    logger.info(
        "discoverable_repos_listed",
        count=len(repos),
        registered=len(registered),
        truncated_env_keys=truncated_env_keys,
        failed=failed,
        cached=cached,
    )
    return DiscoverableReposResponse(repos=repos, truncated_env_keys=truncated_env_keys, cached=cached, failed=failed)
