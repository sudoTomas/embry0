"""Per-sandbox scope for the git-proxy's smart-HTTP forwarding (#521).

The git-proxy used to answer the sandbox's credential helper with the forge
token itself, so anything holding the sandbox bearer got the token, with its
full scope on every repository it can reach. Now the sandbox's git uses the
proxy as its remote and the proxy adds the credential to each upstream request
(ADR 0007). This module decides which requests it forwards:

* **Grant.** Each enrollment carries ``allowed_repos`` (the job's repository,
  as for the forge API proxies in #520), ``git_upstream`` (the forge's base URL
  the sandbox would otherwise clone from) and ``git_push``. The orchestrator
  sends ``[]`` when a job has no repository.
* **Routes.** ``/git/<repo>[.git]/info/refs?service=<svc>`` (GET),
  ``/git/<repo>[.git]/git-upload-pack`` and ``.../git-receive-pack`` (POST).
  Nothing else: no dumb HTTP, no LFS (its batch API hands the client further
  URLs and authorization headers that would have to be rewritten), no query
  other than the one ``info/refs`` needs.
* **Strict path.** Every segment is ``[A-Za-z0-9_.-]``, so there is no
  percent-encoding to interpret; dot segments and empty segments are refused.
  The upstream URL is built from the checked segments.
* **Push.** receive-pack, both its advertisement and the push itself, needs
  ``git_push``. The push's ref updates must also name only the branches in
  ``git_push_branches`` (#559, ADR 0009): the git-proxy reads them from the
  command list (:mod:`embry0.execution.proxy.receive_pack`). The orchestrator
  narrows that list once the developer has chosen its branch, through
  ``PUT /admin/git-push-branches/{sandbox_id}``.
* **Fail closed.** An enrollment without ``allowed_repos`` (an orchestrator
  older than the proxy image) is refused every git request, and one with
  ``git_push`` but without ``git_push_branches`` every ref update.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlsplit

import structlog
from aiohttp import web

from embry0.execution.proxy.enrollment import EnrollRejected, guard_admin
from embry0.execution.proxy.forge_scope import SCOPE_FIELD, normalize_gitlab_project, parse_allowed_repos
from embry0.git_refs import valid_ref

__all__ = [
    "GIT_ROUTE_PREFIX",
    "JOB_FIELD",
    "PUSH_BRANCHES_FIELD",
    "PUSH_FIELD",
    "RECEIVE_PACK",
    "UPLOAD_PACK",
    "UPSTREAM_FIELD",
    "GitGrant",
    "GitScope",
    "GitVerdict",
    "git_verdict",
    "normalize_git_repo",
    "normalize_push_branch",
    "normalize_upstream",
]

logger = structlog.get_logger(__name__)

UPSTREAM_FIELD = "git_upstream"
PUSH_FIELD = "git_push"
PUSH_BRANCHES_FIELD = "git_push_branches"
# Not a credential: the job the sandbox runs, so a refused push is logged with it.
JOB_FIELD = "job_id"
GIT_ROUTE_PREFIX = "/git/"

UPLOAD_PACK = "git-upload-pack"
RECEIVE_PACK = "git-receive-pack"
_SERVICES = (UPLOAD_PACK, RECEIVE_PACK)

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")
_HOST_RE = re.compile(r"^[a-z0-9.-]{1,253}$")
_UPSTREAM_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.~-]{1,255}$")
_JOB_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_MAX_PUSH_BRANCHES = 16


@dataclass(frozen=True)
class GitGrant:
    """What one enrolled sandbox may do through the git-proxy."""

    upstream: str
    repos: frozenset[str]
    push: bool
    # Branch names (no ``refs/heads/``) a push may create or update. Empty with
    # ``push`` means every ref update is refused.
    push_branches: frozenset[str] = frozenset()
    job_id: str = ""


@dataclass(frozen=True)
class GitVerdict:
    """Whether one git request may be forwarded; ``upstream_url`` is set only when it may."""

    allowed: bool
    reason: str = ""
    repo: str = ""
    service: str = ""
    upstream_url: str = ""


def _deny(reason: str) -> GitVerdict:
    return GitVerdict(allowed=False, reason=reason)


def normalize_git_repo(value: str) -> str | None:
    """Repository path lower-cased, or None if malformed.

    A GitHub ``owner/name`` is the two-segment case of a GitLab project path,
    and one git-proxy serves both forges, so the GitLab rule covers both.
    """
    return normalize_gitlab_project(value)


def normalize_push_branch(value: object) -> str | None:
    """A branch name a push may update, or None if git or the proxy would not take it as one.

    Stricter than ``git check-ref-format``: embry0's ref rule
    (:func:`embry0.git_refs.valid_ref`, which workspace init applies to a job's
    ref and every branch embry0 derives stays within), no ``..``, no empty or
    dot-led component, no ``.lock`` component. A ``refs/`` prefix is refused
    rather than stripped, so ``refs/heads/x`` cannot be read two ways.
    """
    if not isinstance(value, str) or not valid_ref(value):
        return None
    if value.startswith("refs/") or ".." in value:
        return None
    for part in value.split("/"):
        if not part or part.startswith(".") or part.endswith(".lock"):
            return None
    if value.endswith("."):
        return None
    return value


def parse_push_branches(raw: object) -> frozenset[str]:
    """``git_push_branches`` from an admin body; raises :class:`EnrollRejected` on any bad entry."""
    if not isinstance(raw, list) or len(raw) > _MAX_PUSH_BRANCHES:
        raise EnrollRejected(f"{PUSH_BRANCHES_FIELD} must be a list of at most {_MAX_PUSH_BRANCHES} branch names")
    branches = set()
    for item in raw:
        branch = normalize_push_branch(item)
        if branch is None:
            raise EnrollRejected(f"{PUSH_BRANCHES_FIELD} entry {str(item)[:200]!r} is not a branch name")
        branches.add(branch)
    return frozenset(branches)


def normalize_upstream(value: str) -> str | None:
    """``scheme://host[:port][/path]`` without a trailing slash, or None.

    Only ``http`` and ``https``; no credentials, query or fragment in the URL,
    and a path of plain segments (a self-managed forge under a relative root).
    """
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        return None
    if parts.query or parts.fragment or not parts.hostname or not _HOST_RE.fullmatch(parts.hostname):
        return None
    path = parts.path.rstrip("/")
    if path:
        segments = path[1:].split("/")
        if not all(_UPSTREAM_PATH_SEGMENT_RE.fullmatch(s) and s not in (".", "..") for s in segments):
            return None
    netloc = parts.hostname if port is None else f"{parts.hostname}:{port}"
    return f"{parts.scheme}://{netloc}{path}"


def git_verdict(method: str, raw_path: str, raw_query: str, grant: GitGrant | None) -> GitVerdict:
    """Decide one request on a ``/git/...`` route against the sandbox's grant."""
    if grant is None:
        return _deny("unscoped_enrollment")
    if not raw_path.startswith(GIT_ROUTE_PREFIX):
        return _deny("path_not_allowed")
    segments = raw_path[len(GIT_ROUTE_PREFIX) :].split("/")
    if not all(_SEGMENT_RE.fullmatch(s) and s not in (".", "..") for s in segments):
        return _deny("malformed_path")

    if segments[-2:] == ["info", "refs"]:
        repo_segments, suffix, expected_method = segments[:-2], "info/refs", "GET"
        service = raw_query[len("service=") :] if raw_query.startswith("service=") else ""
        if service not in _SERVICES:
            return _deny("query_not_allowed")
        query = f"?service={service}"
    elif segments[-1] in _SERVICES:
        repo_segments, suffix, expected_method = segments[:-1], segments[-1], "POST"
        service = segments[-1]
        if raw_query:
            return _deny("query_not_allowed")
        query = ""
    else:
        return _deny("path_not_allowed")
    if method != expected_method:
        return _deny("method_not_allowed")

    if repo_segments and repo_segments[-1].endswith(".git"):
        repo_segments = [*repo_segments[:-1], repo_segments[-1][: -len(".git")]]
    repo = normalize_git_repo("/".join(repo_segments))
    if repo is None:
        return _deny("malformed_path")
    if repo not in grant.repos:
        return _deny("repo_out_of_scope")
    if service == RECEIVE_PACK and not grant.push:
        return _deny("push_not_allowed")

    upstream_url = f"{grant.upstream}/{'/'.join(repo_segments)}.git/{suffix}{query}"
    return GitVerdict(allowed=True, repo=repo, service=service, upstream_url=upstream_url)


class GitScope:
    """Enroll-body ``allowed_repos`` / ``git_upstream`` / ``git_push`` → one :class:`GitGrant`.

    Implements :class:`~embry0.execution.proxy.enrollment.EnrollExtension`.
    Grants live at ``app["git_grant_by_id"]`` and end with their enrollment
    (:meth:`forget`); a sandbox with none is refused every git request. The response echoes the stored grant, which is how the
    orchestrator tells this proxy from an image that predates forwarding.
    """

    def __init__(self, app: web.Application, *, proxy_name: str) -> None:
        self._proxy_name = proxy_name
        app["git_grant_by_id"] = {}

    def parse(self, app: web.Application, body: dict[str, Any]) -> GitGrant | None:
        repos = parse_allowed_repos(body, normalize_git_repo)
        if repos is None:
            return None
        raw_upstream = body.get(UPSTREAM_FIELD)
        upstream = ""
        if raw_upstream is not None:
            normalized = normalize_upstream(raw_upstream)
            if normalized is None:
                raise EnrollRejected(f"{UPSTREAM_FIELD} {str(raw_upstream)[:200]!r} is not an http(s) base URL")
            upstream = normalized
        if repos and not upstream:
            raise EnrollRejected(f"{SCOPE_FIELD} names a repository but there is no {UPSTREAM_FIELD}")
        push = body.get(PUSH_FIELD, False)
        if not isinstance(push, bool):
            raise EnrollRejected(f"{PUSH_FIELD} must be true or false")
        raw_branches = body.get(PUSH_BRANCHES_FIELD)
        branches = frozenset() if raw_branches is None else parse_push_branches(raw_branches)
        job_id = body.get(JOB_FIELD)
        if job_id is not None and (not isinstance(job_id, str) or not _JOB_RE.fullmatch(job_id)):
            raise EnrollRejected(f"{JOB_FIELD} {str(job_id)[:200]!r} is not a job id")
        return GitGrant(upstream=upstream, repos=repos, push=push, push_branches=branches, job_id=job_id or "")

    def apply(self, app: web.Application, sandbox_id: str, parsed: GitGrant | None) -> dict[str, Any]:
        grants: dict[str, GitGrant] = app["git_grant_by_id"]
        if parsed is None:
            grants.pop(sandbox_id, None)
            logger.warning(
                f"{self._proxy_name}_enrollment_unscoped",
                sandbox_id=sandbox_id,
                detail=f"no {SCOPE_FIELD} in the enrollment; every git request from this sandbox will be refused",
            )
            return {}
        grants[sandbox_id] = parsed
        if parsed.push and not parsed.push_branches:
            logger.warning(
                f"{self._proxy_name}_enrollment_without_push_branches",
                sandbox_id=sandbox_id,
                job_id=parsed.job_id,
                detail=f"git_push without {PUSH_BRANCHES_FIELD}; every push from this sandbox will be refused",
            )
        return _echo(parsed)

    def register_routes(self, app: web.Application) -> None:
        """``PUT /admin/git-push-branches/{sandbox_id}``: replace an enrolled sandbox's push branches.

        The developer chooses its branch after the sandbox is enrolled (#478
        probes origin from inside it), so the orchestrator enrolls the job's
        own branch and swaps in the chosen one here. Admin-only, like enroll;
        404 for a sandbox with no grant, 409 for one without the push right.
        """

        async def _handler(request: web.Request) -> web.Response:
            denied = guard_admin(request, self._proxy_name)
            if denied is not None:
                return denied
            sandbox_id = request.match_info["sandbox_id"]
            try:
                body = await request.json()
            except ValueError:
                return web.json_response({"error": "bad_request"}, status=400)
            if not isinstance(body, dict):
                return web.json_response({"error": "bad_request"}, status=400)
            try:
                branches = parse_push_branches(body.get(PUSH_BRANCHES_FIELD))
            except EnrollRejected as exc:
                return web.json_response({"error": "bad_request", "detail": str(exc)}, status=400)
            grants: dict[str, GitGrant] = request.app["git_grant_by_id"]
            grant = grants.get(sandbox_id)
            if grant is None:
                return web.json_response({"error": "not_enrolled"}, status=404)
            if not grant.push:
                return web.json_response({"error": "push_not_allowed"}, status=409)
            grants[sandbox_id] = replace(grant, push_branches=branches)
            logger.info(
                f"{self._proxy_name}_push_branches_replaced",
                sandbox_id=sandbox_id,
                job_id=grant.job_id,
                before=sorted(grant.push_branches),
                after=sorted(branches),
            )
            return web.json_response(_echo(grants[sandbox_id]))

        app.router.add_put("/admin/git-push-branches/{sandbox_id}", _handler)

    def forget(self, app: web.Application, sandbox_id: str) -> None:
        """``on_unenroll`` hook: the registry runs it whenever an enrollment ends (#536).

        Revoked, expired or replaced, a grant never outlives its enrollment, so
        the map is bounded by the registry's cap.
        """
        app["git_grant_by_id"].pop(sandbox_id, None)


def _echo(grant: GitGrant) -> dict[str, Any]:
    """The grant as the enroll response states it; the orchestrator compares it with what it sent."""
    return {
        SCOPE_FIELD: sorted(grant.repos),
        UPSTREAM_FIELD: grant.upstream,
        PUSH_FIELD: grant.push,
        PUSH_BRANCHES_FIELD: sorted(grant.push_branches),
    }
