"""Per-sandbox repository scope for the forge API proxies (#520).

The github-proxy and gitlab-proxy inject the forge credential into every call
they forward. They used to forward any method and any path, so anything inside
a sandbox holding its proxy bearer (the bearer sits in git's config) could use
that credential's full scope on every repository it reaches.

No sandbox-side code calls these proxies today: pull requests, merge requests,
comments and QA reports are posted by the orchestrator's own forge clients,
straight to the forge. The policy below is therefore the narrowest useful
contract rather than a record of observed calls:

* **Scope.** Each enrollment carries ``allowed_repos``: the job's repository,
  ``owner/name`` on GitHub or the full project path on GitLab. The
  orchestrator sends ``[]`` to the other forge's API proxy, and to both when a
  job has no repository.
* **Allowlist.** Read calls (``GET``/``HEAD``) on a fixed set of resources of a
  repository in scope. No write method, no GraphQL, nothing outside
  ``/repos/<owner>/<name>/...`` or ``/projects/<path>/...``. Adding a write is a
  deliberate edit to the rule table, made together with the caller that needs it.
* **Canonical paths.** The path is checked segment by segment in its raw,
  percent-encoded form. Dot segments, empty segments (a double or trailing
  slash), backslashes, control characters, double encoding and encoded slashes
  outside the places a forge needs them are refused. The upstream path is
  rebuilt from the checked segments, so what is forwarded is what was checked:
  httpx would otherwise collapse ``repos/a/b/../../c/d`` into ``repos/c/d``
  after the check.
* **Fail closed.** An enrollment without ``allowed_repos`` (an orchestrator
  older than the proxy image) has every call refused, with a warning when it is
  enrolled and on every refusal.

Every refusal is a 403 ``policy_denied`` with a stable ``reason`` and a
``<proxy>_policy_denied`` warning line.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote

import structlog
from aiohttp import web

from embry0.execution.proxy.enrollment import EnrollRejected

__all__ = [
    "MAX_SCOPE_REPOS",
    "REPO_SCOPE_BY_ID",
    "SCOPE_FIELD",
    "RepoScope",
    "Verdict",
    "github_verdict",
    "gitlab_verdict",
    "normalize_github_repo",
    "normalize_gitlab_project",
    "parse_allowed_repos",
    "policy_denied",
    "request_verdict",
]

logger = structlog.get_logger(__name__)

SCOPE_FIELD = "allowed_repos"
# A sandbox works on one repository. A few more leave room for a future
# multi-repository job without letting an enroll body grow without bound.
MAX_SCOPE_REPOS = 8

_READ = frozenset({"GET", "HEAD"})

# Left unescaped when a checked segment is re-encoded: RFC 3986 unreserved
# characters, sub-delims, ":" and "@" (pchar). Never "/": a decoded slash inside
# one segment goes upstream as %2F, exactly as the forge expects it.
_PCHAR_SAFE = "-._~!$&'()*+,;=:@"

_NAME_PART_RE = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")
_GITLAB_MAX_DEPTH = 20
_DIGITS_RE = re.compile(r"^[0-9]+$")
_ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")

# Rule tokens. Anything else in a pattern is a literal, matched case-sensitively.
_NUM = "{num}"  # ASCII digits
_SEG = "{seg}"  # one segment with no encoded slash
_ENC = "{enc}"  # one segment that may carry encoded slashes (GitLab file paths, refs)
_REST = "{rest}"  # one or more trailing segments, none with an encoded slash (GitHub file paths)

_DETAIL = "this proxy forwards read calls on the job's own repository only"


@dataclass(frozen=True)
class _Rule:
    methods: frozenset[str]
    pattern: tuple[str, ...]


def _read(*pattern: str) -> _Rule:
    return _Rule(_READ, pattern)


# Relative to /repos/{owner}/{name}.
GITHUB_RULES: tuple[_Rule, ...] = (
    _read(),
    _read("issues"),
    _read("issues", _NUM),
    _read("issues", _NUM, "comments"),
    _read("issues", _NUM, "events"),
    _read("issues", _NUM, "timeline"),
    _read("issues", "comments"),
    _read("issues", "comments", _NUM),
    _read("pulls"),
    _read("pulls", _NUM),
    _read("pulls", _NUM, "commits"),
    _read("pulls", _NUM, "files"),
    _read("pulls", _NUM, "comments"),
    _read("pulls", _NUM, "reviews"),
    _read("pulls", _NUM, "reviews", _NUM),
    _read("pulls", _NUM, "reviews", _NUM, "comments"),
    _read("pulls", "comments"),
    _read("pulls", "comments", _NUM),
    _read("commits"),
    _read("commits", _SEG),
    _read("commits", _SEG, "check-runs"),
    _read("commits", _SEG, "check-suites"),
    _read("commits", _SEG, "status"),
    _read("commits", _SEG, "statuses"),
    _read("check-runs", _NUM),
    _read("check-runs", _NUM, "annotations"),
    _read("check-suites", _NUM),
    _read("check-suites", _NUM, "check-runs"),
    _read("branches"),
    _read("branches", _SEG),
    _read("compare", _REST),
    _read("contents"),
    _read("contents", _REST),
    _read("readme"),
    _read("labels"),
)

# Relative to /projects/{url-encoded project path}.
GITLAB_RULES: tuple[_Rule, ...] = (
    _read(),
    _read("issues"),
    _read("issues", _NUM),
    _read("issues", _NUM, "notes"),
    _read("issues", _NUM, "notes", _NUM),
    _read("issues", _NUM, "discussions"),
    _read("merge_requests"),
    _read("merge_requests", _NUM),
    _read("merge_requests", _NUM, "notes"),
    _read("merge_requests", _NUM, "notes", _NUM),
    _read("merge_requests", _NUM, "discussions"),
    _read("merge_requests", _NUM, "changes"),
    _read("merge_requests", _NUM, "diffs"),
    _read("merge_requests", _NUM, "commits"),
    _read("merge_requests", _NUM, "pipelines"),
    _read("repository", "commits"),
    _read("repository", "commits", _ENC),
    _read("repository", "commits", _ENC, "diff"),
    _read("repository", "commits", _ENC, "statuses"),
    _read("repository", "tree"),
    _read("repository", "files", _ENC),
    _read("repository", "files", _ENC, "raw"),
    _read("repository", "branches"),
    _read("repository", "branches", _ENC),
    _read("repository", "compare"),
    _read("pipelines"),
    _read("pipelines", _NUM),
    _read("pipelines", _NUM, "jobs"),
    _read("labels"),
)


@dataclass(frozen=True)
class Verdict:
    """Whether one call may be forwarded; ``upstream_path`` is set only when it may."""

    allowed: bool
    reason: str = ""
    upstream_path: str = ""


def _deny(reason: str) -> Verdict:
    return Verdict(allowed=False, reason=reason)


def _safe_parts(parts: list[str]) -> bool:
    return all(p and p not in (".", "..") for p in parts)


def normalize_github_repo(value: str) -> str | None:
    """``owner/name`` lower-cased (GitHub names are case-insensitive), or None if malformed."""
    parts = value.strip().split("/")
    if len(parts) != 2 or not _safe_parts(parts) or not all(_NAME_PART_RE.fullmatch(p) for p in parts):
        return None
    return "/".join(parts).lower()


def normalize_gitlab_project(value: str) -> str | None:
    """Full project path lower-cased (GitLab resolves paths case-insensitively), or None."""
    parts = value.strip().split("/")
    if not 2 <= len(parts) <= _GITLAB_MAX_DEPTH:
        return None
    if not _safe_parts(parts) or not all(_NAME_PART_RE.fullmatch(p) for p in parts):
        return None
    return "/".join(parts).lower()


def _decode_segments(raw_path: str) -> list[str] | None:
    """Percent-decode each segment of a raw request path; None when any is unsafe.

    Refuses: no leading slash, an empty segment, a dot segment in raw or encoded
    form, a backslash, a control character, and an escape that survives one
    decoding (double encoding). A decoded "/" is kept; the caller decides where
    an encoded slash is legal.
    """
    if not raw_path.startswith("/"):
        return None
    body = raw_path[1:]
    if not body:
        return []
    segments: list[str] = []
    for raw in body.split("/"):
        if not raw or "\\" in raw:
            return None
        try:
            decoded = unquote(raw, errors="strict")
        except UnicodeDecodeError:
            return None
        if decoded in (".", "..") or "\\" in decoded or _ESCAPE_RE.search(decoded):
            return None
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in decoded):
            return None
        segments.append(decoded)
    return segments


def _token_matches(token: str, segment: str) -> bool:
    if token == _NUM:
        return bool(_DIGITS_RE.fullmatch(segment))
    if token == _SEG:
        return "/" not in segment
    if token == _ENC:
        return _safe_parts(segment.split("/"))
    return segment == token


def _match(rules: tuple[_Rule, ...], rest: list[str]) -> _Rule | None:
    for rule in rules:
        pattern = rule.pattern
        if pattern and pattern[-1] == _REST:
            head = pattern[:-1]
            tail = rest[len(head) :]
            if len(rest) <= len(head) or any("/" in s for s in tail):
                continue
        else:
            head = pattern
            if len(rest) != len(head):
                continue
        if all(_token_matches(t, s) for t, s in zip(head, rest, strict=False)):
            return rule
    return None


def _encode(segments: list[str]) -> str:
    return "/" + "/".join(quote(s, safe=_PCHAR_SAFE) for s in segments)


def _verdict(method: str, segments: list[str], prefix_len: int, rules: tuple[_Rule, ...]) -> Verdict:
    rule = _match(rules, segments[prefix_len:])
    if rule is None:
        return _deny("path_not_allowed")
    if method not in rule.methods:
        return _deny("method_not_allowed")
    return Verdict(allowed=True, upstream_path=_encode(segments))


def github_verdict(method: str, raw_path: str, scope: frozenset[str] | None) -> Verdict:
    """Decide one call to the GitHub REST API: ``/repos/<owner>/<name>/...`` in scope, read only."""
    if scope is None:
        return _deny("unscoped_enrollment")
    segments = _decode_segments(raw_path)
    if segments is None or any("/" in s for s in segments):
        return _deny("malformed_path")
    if len(segments) < 3 or segments[0] != "repos":
        return _deny("path_not_allowed")
    repo = normalize_github_repo(f"{segments[1]}/{segments[2]}")
    if repo is None:
        return _deny("malformed_path")
    if repo not in scope:
        return _deny("repo_out_of_scope")
    return _verdict(method, segments, 3, GITHUB_RULES)


def gitlab_verdict(method: str, raw_path: str, scope: frozenset[str] | None) -> Verdict:
    """Decide one call to the GitLab REST v4 API: ``/projects/<encoded path>/...`` in scope, read only.

    A numeric project id is refused: the scope names paths, and mapping an id to
    its path would take an upstream call made with the credential being guarded.
    """
    if scope is None:
        return _deny("unscoped_enrollment")
    segments = _decode_segments(raw_path)
    if segments is None:
        return _deny("malformed_path")
    if len(segments) < 2 or segments[0] != "projects":
        return _deny("path_not_allowed")
    if _DIGITS_RE.fullmatch(segments[1]):
        return _deny("project_id_not_a_path")
    project = normalize_gitlab_project(segments[1])
    if project is None:
        return _deny("malformed_path")
    if project not in scope:
        return _deny("repo_out_of_scope")
    return _verdict(method, segments, 2, GITLAB_RULES)


def parse_allowed_repos(body: dict[str, Any], normalize: Callable[[str], str | None]) -> frozenset[str] | None:
    """The enroll body's ``allowed_repos``, normalized; None when the field is absent.

    Raises :class:`EnrollRejected` for anything but a short list of repository paths.
    """
    if SCOPE_FIELD not in body:
        return None
    requested = body[SCOPE_FIELD]
    if not isinstance(requested, list) or not all(isinstance(r, str) for r in requested):
        raise EnrollRejected(f"{SCOPE_FIELD} must be a list of repository paths")
    if len(requested) > MAX_SCOPE_REPOS:
        raise EnrollRejected(f"{SCOPE_FIELD} names more than {MAX_SCOPE_REPOS} repositories")
    scope: set[str] = set()
    for value in requested:
        normalized = normalize(value)
        if normalized is None:
            raise EnrollRejected(f"{SCOPE_FIELD} entry {value[:200]!r} is not a repository path")
        scope.add(normalized)
    return frozenset(scope)


#: Each enrolled sandbox's repositories, by sandbox id (:class:`RepoScope`).
REPO_SCOPE_BY_ID: web.AppKey[dict[str, frozenset[str]]] = web.AppKey("repo_scope_by_id")


class RepoScope:
    """Enroll-body ``allowed_repos`` → the repositories one sandbox may reach.

    Implements :class:`~embry0.execution.proxy.enrollment.EnrollExtension`.
    The scope lives at ``app[REPO_SCOPE_BY_ID]``; a sandbox with no entry is
    unscoped and refused everything.
    """

    def __init__(self, app: web.Application, *, proxy_name: str, normalize: Callable[[str], str | None]) -> None:
        self._proxy_name = proxy_name
        self._normalize = normalize
        app[REPO_SCOPE_BY_ID] = {}

    def parse(self, app: web.Application, body: dict[str, Any]) -> frozenset[str] | None:
        return parse_allowed_repos(body, self._normalize)

    def apply(self, app: web.Application, sandbox_id: str, parsed: frozenset[str] | None) -> dict[str, Any]:
        scopes = app[REPO_SCOPE_BY_ID]
        if parsed is None:
            scopes.pop(sandbox_id, None)
            logger.warning(
                f"{self._proxy_name}_enrollment_unscoped",
                sandbox_id=sandbox_id,
                detail=f"no {SCOPE_FIELD} in the enrollment; every forwarded call from this sandbox will be refused",
            )
            return {}
        scopes[sandbox_id] = parsed
        return {SCOPE_FIELD: sorted(parsed)}

    def forget(self, app: web.Application, sandbox_id: str) -> None:
        """``on_unenroll`` hook: the registry runs it whenever an enrollment ends (#536)."""
        app[REPO_SCOPE_BY_ID].pop(sandbox_id, None)


def request_verdict(
    request: web.Request,
    sandbox_id: str,
    evaluate: Callable[[str, str, frozenset[str] | None], Verdict],
) -> Verdict:
    """Apply ``evaluate`` to this request with the sandbox's enrolled scope."""
    scope = request.app[REPO_SCOPE_BY_ID].get(sandbox_id)
    return evaluate(request.method, request.rel_url.raw_path, scope)


def policy_denied(request: web.Request, proxy_name: str, sandbox_id: str, verdict: Verdict) -> web.Response:
    """The 403 for a refused call, logged as ``<proxy>_policy_denied``."""
    logger.warning(
        f"{proxy_name}_policy_denied",
        sandbox_id=sandbox_id,
        method=request.method[:16],
        path=request.rel_url.raw_path[:256],
        reason=verdict.reason,
    )
    return web.json_response({"error": "policy_denied", "reason": verdict.reason, "detail": _DETAIL}, status=403)
