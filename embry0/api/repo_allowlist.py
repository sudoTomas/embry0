"""Per-API-key repository allowlist.

An API key may carry ``allowed_repos``: the repositories it may start,
continue or stop work on. ``None`` — every key minted before the column existed, and
every key minted without a list — is unrestricted. Sessions and the master
key are never restricted.

Each route that starts, continues or stops work (cancelling a job, deleting
an issue), and each step of setting up testing (apply or turn down a setup
proposal, skip testing, undo a change), calls :func:`enforce_repo_allowlist`
with the repository it is about to act on. The check lives in the API, not in
a client: the MCP server reaches embry0 through these routes with the
caller's own key, so a client that holds a restricted key cannot route
around it.

Matching is case-insensitive (forge slugs are) and goes through
``REPO_RENAMES``, so an allowlist that names a repository by its old slug
still matches work addressed by the new one, and the reverse.

A refusal is a 403 whose detail starts with :data:`REFUSAL_CODE`, a
structured ``api_key_repo_refused`` log line naming the key (never its
secret) and the refused repository, and an ``apikey.repo_refused`` audit row.
"""

from __future__ import annotations

from collections.abc import Iterable

import structlog
from fastapi import HTTPException, Request

from embry0.audit.helpers import emit_audit
from embry0.storage.repo_renames import normalize_repo_slug

logger = structlog.get_logger(__name__)

REFUSAL_CODE = "repo_not_allowed"


def _canonical(slug: str) -> str:
    return normalize_repo_slug(slug.strip()).lower()


def repo_allowed(allowed_repos: Iterable[str] | None, repo: str | None) -> bool:
    """Whether a key with this allowlist may act on ``repo``.

    ``allowed_repos`` None is unrestricted. A restricted key may not act on
    work that names no repository: there is nothing to check it against.
    """
    if allowed_repos is None:
        return True
    if not repo:
        return False
    target = _canonical(repo)
    return any(_canonical(entry) == target for entry in allowed_repos)


def restricted_key_name(request: Request) -> str | None:
    """The key's name when the caller is a key with an allowlist, else None."""
    principal = getattr(request.state, "principal", None)
    if principal is None or principal.allowed_repos is None:
        return None
    return str(principal.key_name)


def refusal_detail(request: Request, repo: str | None, *, action: str) -> str | None:
    """The 403 detail :func:`enforce_repo_allowlist` would raise, or None; no log, no audit row.

    For a caller that wants the answer without the refusal, such as a dry run.
    """
    principal = getattr(request.state, "principal", None)
    if principal is None or repo_allowed(principal.allowed_repos, repo):
        return None
    shown = repo or "(no repository)"
    return (
        f"{REFUSAL_CODE}: API key {principal.key_name!r} may not {action} on {shown}; "
        "the key is limited to a repository allowlist that does not include it"
    )


async def enforce_repo_allowlist(request: Request, repo: str | None, *, action: str) -> None:
    """Refuse with 403 when the calling key may not ``action`` on ``repo``.

    ``action`` is a short verb phrase for the log and the error ("create a
    job", "resume a job"). No-op for sessions, the master key, AUTH_DEV_MODE
    and unrestricted keys.
    """
    detail = refusal_detail(request, repo, action=action)
    if detail is None:
        return
    principal = request.state.principal
    shown = repo or "(no repository)"
    logger.warning(
        "api_key_repo_refused",
        key_name=principal.key_name,
        key_id=principal.key_id,
        repo=shown,
        action=action,
        method=request.method,
        path=request.url.path,
    )
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        "apikey.repo_refused",
        actor=f"key:{principal.key_name}",
        details={"key_id": principal.key_id, "key_name": principal.key_name, "repo": shown, "action": action},
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    raise HTTPException(status_code=403, detail=detail)
