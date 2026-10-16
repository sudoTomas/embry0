"""GitLab REST v4 client (#164).

Inherits retry, backoff, lifecycle and pagination from
:class:`embry0.execution.forge_client.ForgeClient`; only what is genuinely
GitLab-specific lives here.

Three differences from GitHub matter and are easy to get wrong:

**Auth header.** GitLab reads ``PRIVATE-TOKEN`` for personal and group access
tokens. ``Authorization: Bearer`` is only correct for OAuth2 tokens, and a
``glpat-`` PAT sent that way is rejected — so the token shape decides the
header.

**Project identity.** Endpoints take a project *id*, and a path used in its
place must be URL-encoded **including the slashes**:
``acme%2Fwidgets%2Fshop``. Leaving them raw silently addresses a
different route and 404s.

**Self-managed hosts.** There is no fixed API origin; ``GITLAB_URL`` supplies
it and ``/api/v4`` is appended.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx
import structlog

from embry0.execution.forge_client import ForgeClient, ForgeWrite, forge_write
from embry0.execution.gitlab_tokens import resolve_for_repo
from embry0.execution.tls import build_ssl_context, load_ca_pem

logger = structlog.get_logger(__name__)

DEFAULT_GITLAB_URL = "https://gitlab.com"
API_PREFIX = "/api/v4"

# GitLab's own prefixes for OAuth2 access tokens. Anything else — notably a
# `glpat-` personal access token — must go in PRIVATE-TOKEN instead.
_OAUTH_TOKEN_PREFIXES = ("gloas-", "ya29.")


def api_base(gitlab_url: str = DEFAULT_GITLAB_URL) -> str:
    """API origin for a gitlab.com or self-managed host."""
    return f"{(gitlab_url or DEFAULT_GITLAB_URL).rstrip('/')}{API_PREFIX}"


def encode_project(repo: str) -> str:
    """URL-encode a project path for use as a project id.

    ``safe=""`` is the whole point: the slashes MUST become ``%2F`` or the
    request addresses a different route entirely.
    """
    return quote(repo, safe="")


class GitLabClient(ForgeClient):
    """Thin, retrying wrapper over the GitLab REST v4 API."""

    forge = "gitlab"

    def __init__(self, *, gitlab_url: str = DEFAULT_GITLAB_URL, ca_pem: str = "", **kwargs: Any) -> None:
        # Spec 1A §3.2. setdefault, not assignment: a caller passing its own
        # ``verify`` (a pinned context, a test transport's) still wins.
        if ca_pem:
            kwargs.setdefault("verify", build_ssl_context(ca_pem))
        super().__init__(base_url=api_base(gitlab_url), **kwargs)
        self._gitlab_url = (gitlab_url or DEFAULT_GITLAB_URL).rstrip("/")

    @property
    def gitlab_url(self) -> str:
        """Web origin (not the API base) — for building human-facing links."""
        return self._gitlab_url

    def headers(self, repo: str | None = None, *, token: str | None = None, **_: object) -> dict[str, str]:
        """``token`` from the registry wins; None runs the env lookup."""
        built = {"Accept": "application/json"}
        if token is None:
            token = resolve_for_repo(repo, self._default_token)
        if token:
            if token.startswith(_OAUTH_TOKEN_PREFIXES):
                built["Authorization"] = f"Bearer {token}"
            else:
                built["PRIVATE-TOKEN"] = token
        return built

    # -- convenience ------------------------------------------------------

    def project_path(self, repo: str, suffix: str = "") -> str:
        """``/projects/<encoded>`` plus an optional suffix."""
        return f"/projects/{encode_project(repo)}{suffix}"

    async def get_project(self, repo: str) -> dict[str, object] | None:
        """Project record, or None when it is missing or not visible."""
        resp = await self.get(self.project_path(repo), repo=repo)
        if resp.status_code != 200:
            return None
        body = resp.json()
        return body if isinstance(body, dict) else None

    async def get_issue(self, repo: str, iid: int) -> dict[str, object] | None:
        """One issue by its project-scoped ``iid`` (not the global id)."""
        resp = await self.get(self.project_path(repo, f"/issues/{iid}"), repo=repo)
        if resp.status_code != 200:
            return None
        body = resp.json()
        return body if isinstance(body, dict) else None

    async def get_merge_request(self, repo: str, iid: int) -> dict[str, Any] | None:
        """One merge request by its project-scoped ``iid``, or None when missing or unreadable."""
        resp = await self.get(self.project_path(repo, f"/merge_requests/{iid}"), repo=repo)
        if resp.status_code != 200:
            return None
        body = resp.json()
        return body if isinstance(body, dict) else None

    async def get_member_access_level(self, repo: str, user_id: int) -> int | None:
        """The user's access level on the project, inherited memberships included.

        ``/members/all`` counts group membership too; plain ``/members`` would
        refuse a Developer who is one only through the group. None when the
        user is not a member or the lookup failed.
        """
        resp = await self.get(self.project_path(repo, f"/members/all/{user_id}"), repo=repo)
        if resp.status_code != 200:
            return None
        body = resp.json()
        level = body.get("access_level") if isinstance(body, dict) else None
        return level if isinstance(level, int) and not isinstance(level, bool) else None

    async def create_issue_note(self, repo: str, iid: int, body: str) -> bool:
        """Post a note (comment) on an issue. True when accepted."""
        resp = await self.post(
            self.project_path(repo, f"/issues/{iid}/notes"),
            repo=repo,
            json={"body": body},
        )
        return resp.status_code in (200, 201)

    async def post_mr_note(self, repo: str, iid: int, body: str, *, retry_transient: bool = False) -> ForgeWrite:
        """Post a note on a merge request; what the forge answered.

        Sent once by default: a note GitLab accepted and answered 502 must not
        be posted twice.
        """
        resp = await self.post(
            self.project_path(repo, f"/merge_requests/{iid}/notes"),
            repo=repo,
            json={"body": body},
            retry_transient=retry_transient,
        )
        return forge_write(resp)

    async def create_mr_note(self, repo: str, iid: int, body: str) -> bool:
        """Post a note (comment) on a merge request. True when accepted.

        Keeps the default retry policy its callers had before ``post_mr_note``.
        """
        return (await self.post_mr_note(repo, iid, body, retry_transient=True)).ok

    async def create_mr_discussion(self, repo: str, iid: int, *, body: str, position: dict[str, Any]) -> ForgeWrite:
        """Start a diff discussion at ``position`` (#30); GitLab answers 400 for a line outside the diff."""
        resp = await self.post(
            self.project_path(repo, f"/merge_requests/{iid}/discussions"),
            repo=repo,
            json={"body": body, "position": position},
            retry_transient=False,  # sent once: an accepted discussion answered 502 must not be posted twice
        )
        return forge_write(resp)

    async def list_merge_request_diffs(self, repo: str, iid: int) -> list[dict[str, Any]]:
        """The merge request's file diffs (#30).

        ``/diffs`` exists from GitLab 15.7; an older instance answers 404 there
        and serves the same items under ``/changes``. Raises on any other error.
        """
        try:
            items = await self.paginate(self.project_path(repo, f"/merge_requests/{iid}/diffs"), repo=repo, strict=True)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            resp = await self.get(self.project_path(repo, f"/merge_requests/{iid}/changes"), repo=repo)
            resp.raise_for_status()
            body = resp.json()
            items = body.get("changes") or [] if isinstance(body, dict) else []
        return [item for item in items if isinstance(item, dict)]

    async def add_mr_labels(self, repo: str, iid: int, labels: list[str]) -> bool:
        """Add labels to a merge request without touching the ones it has."""
        resp = await self.put(
            self.project_path(repo, f"/merge_requests/{iid}"),
            repo=repo,
            json={"add_labels": ",".join(labels)},
        )
        return resp.status_code in (200, 201)


def parse_repo_map(raw: str) -> dict[str, str]:
    """Parse ``GITLAB_REPO_MAP`` — GitLab project path → target repo.

    Unlike Linear, where a project name bears no relation to a repo, a GitLab
    project *is* the repo. So the map is an override, not a requirement:
    resolution falls back to identity (see :func:`resolve_target_repo`), and
    an empty map is the normal configuration.

    Malformed JSON yields an empty map with a warning rather than refusing to
    start — a typo here must not take the whole orchestrator down, and the
    identity fallback keeps the common case working.
    """
    if not raw or not raw.strip():
        return {}
    import json

    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("gitlab_repo_map_invalid_json", length=len(raw))
        return {}
    if not isinstance(parsed, dict):
        logger.warning("gitlab_repo_map_not_an_object", got=type(parsed).__name__)
        return {}
    return {str(k): str(v) for k, v in parsed.items() if k and v}


def resolve_target_repo(project_path: str, repo_map: dict[str, str]) -> str:
    """Target repo for a GitLab project path.

    Identity unless the map says otherwise. The webhook secret and the repo
    registry are what gate which projects may dispatch; requiring a map entry
    as well would make the common single-instance setup needlessly fiddly.
    """
    if not project_path:
        return ""
    return repo_map.get(project_path) or project_path


def make_gitlab_client(config: Any, credential_resolver: Any | None = None, **kwargs: Any) -> GitLabClient:
    """The one way to build a :class:`GitLabClient` from configuration.

    Four call sites used to spell out ``default_token`` and ``gitlab_url``
    independently; the CA bundle (spec 1A §3.2) would have made that three
    things to keep in step, and the one that drifted would fail as a TLS
    handshake error far from its cause.

    ``config`` is duck-typed (``gitlab_token``, ``gitlab_url``,
    ``gitlab_ca_bundle``) so the executor's partially-populated config object
    works alongside :class:`~embry0.config.Embry0Config`. Extra kwargs reach
    :class:`ForgeClient` unchanged — that is how tests inject a transport.

    Raises :class:`~embry0.execution.tls.CABundleError` if the configured
    bundle has become unreadable since startup validated it.
    """
    return GitLabClient(
        default_token=str(getattr(config, "gitlab_token", "") or ""),
        gitlab_url=str(getattr(config, "gitlab_url", "") or DEFAULT_GITLAB_URL),
        ca_pem=load_ca_pem(str(getattr(config, "gitlab_ca_bundle", "") or "")),
        credential_resolver=credential_resolver,
        **kwargs,
    )
