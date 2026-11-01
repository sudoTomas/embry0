"""Per-API-key repository allowlist.

An API key may carry ``allowed_repos``: the repositories it may read, change and start,
continue or stop work on. ``None`` — every key minted before the column existed, and
every key minted without a list — is unrestricted. Sessions and the master
key are never restricted.

Each route that starts, continues or stops work (cancelling a job, deleting
an issue), each step of setting up testing (apply or turn down a setup
proposal, skip testing, undo a change), and each route that changes a
repository's registration or configuration (its variables, preferences,
test setup, credentials, agent context, standard or watched stack) calls
:func:`enforce_repo_allowlist` with the repository it is about to act on,
before it reads anything, so the answer is the same whether the repository
is registered or not. Each route that changes an organization calls
:func:`enforce_owner_allowlist`. The check lives in the API, not in
a client: the MCP server reaches embry0 through these routes with the
caller's own key, so a client that holds a restricted key cannot route
around it.

Matching is case-insensitive (forge slugs are) and goes through
``REPO_RENAMES``, so an allowlist that names a repository by its old slug
still matches work addressed by the new one, and the reverse.

A refusal is a 403 whose detail starts with :data:`REFUSAL_CODE`, a
structured ``api_key_repo_refused`` log line naming the key (never its
secret) and the refused repository, and an ``apikey.repo_refused`` audit row.
The row's actor is ``embry0:repo-allowlist`` (``dispatch_actors.REPO_ALLOWLIST``), not the key, with the key and its
caller in the details, and ``GET /audit`` never shows such a row to a key
with an allowlist: an action on an item the key does not cover leaves
nothing it can read back that an action on a missing item would not.

Something that belongs to a forge owner rather than to one repository (a
knowledge map, an organization and its needs) is checked with
:func:`enforce_owner_allowlist`: a key covers
the owners of the repositories on its list, and the refusal is the same,
naming ``<owner>/*``.

A route that lists or reads a repository's decisions and the work offered
on it (quests to review, quests on offer, proposals, leads, asks, hand-offs)
and the game layer's view of its work asks :func:`key_covers`: a list leaves
out what the key does not cover, and reading one such item answers as an
item that does not exist does (:func:`hide_unless_covered`). Nothing was
refused, so nothing is logged or audited. A route that acts on one such item
answers the same, and logs and audits the refusal. A list that pages in SQL
narrows its query to :func:`covered_slugs` and still keeps only what
:func:`key_covers` passes.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NoReturn

import structlog
from fastapi import HTTPException, Request

from embry0.api.deps import Principal, request_actor
from embry0.audit.helpers import emit_audit
from embry0.orchestration.dispatch_actors import REPO_ALLOWLIST
from embry0.storage.repo_renames import normalize_repo_slug, repo_slug_variants

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


def _owners(entry: str) -> set[str]:
    """The owners an allowlist entry covers: its own, and through ``REPO_RENAMES`` its current and old ones."""
    slugs = [entry.strip(), *repo_slug_variants(entry.strip())]
    return {slug.lower().split("/", 1)[0] for slug in slugs if "/" in slug}


def owner_allowed(allowed_repos: Iterable[str] | None, owner: str | None) -> bool:
    """Whether a key with this allowlist covers ``owner``: one of its repositories is under it.

    ``allowed_repos`` None is unrestricted. A restricted key covers no
    ``owner`` None, as it may not act on work that names no repository.
    """
    if allowed_repos is None:
        return True
    if not owner:
        return False
    target = owner.strip().lower()
    return any(target in _owners(entry) for entry in allowed_repos)


def key_covers(request: Request, repo: str | None) -> bool:
    """Whether the caller's key covers ``repo``: always for sessions, the master key, AUTH_DEV_MODE and
    keys without an allowlist; for a key with one, whether ``repo`` is on it. Logs and audits nothing."""
    principal = getattr(request.state, "principal", None)
    return principal is None or repo_allowed(principal.allowed_repos, repo)


def key_covers_owner(request: Request, owner: str | None) -> bool:
    """Whether the caller's key covers ``owner``: one of its repositories is under it. As :func:`key_covers`,
    always for sessions, the master key, AUTH_DEV_MODE and keys without an allowlist; logs and audits nothing."""
    principal = getattr(request.state, "principal", None)
    return principal is None or owner_allowed(principal.allowed_repos, owner)


def covered_slugs(request: Request) -> list[str] | None:
    """The lower-cased slugs a stored row may carry for a repository the caller's key covers, with each
    one's old names from ``REPO_RENAMES``: what a list that pages in SQL narrows its query to. None when the
    key covers every repository. A row it selects is one :func:`key_covers` passes."""
    principal = getattr(request.state, "principal", None)
    if principal is None or principal.allowed_repos is None:
        return None
    return sorted({v.lower() for entry in principal.allowed_repos for v in repo_slug_variants(entry.strip())})


def wider_than_allowlist(request: Request, allowed_repos: Iterable[str] | None) -> str | None:
    """What of another key's allowlist ``allowed_repos`` the caller's key does not cover, or None when it covers
    all of it: ``*`` for a key without an allowlist, else the first repository its own list leaves out. Always
    None for sessions, the master key, AUTH_DEV_MODE and keys without an allowlist."""
    principal = getattr(request.state, "principal", None)
    if principal is None or principal.allowed_repos is None:
        return None
    if allowed_repos is None:
        return "*"
    return next((repo for repo in allowed_repos if not repo_allowed(principal.allowed_repos, repo)), None)


def restricted_key_name(request: Request) -> str | None:
    """The key's name when the caller is a key with an allowlist, else None."""
    principal = getattr(request.state, "principal", None)
    if principal is None or principal.allowed_repos is None:
        return None
    return str(principal.key_name)


def _detail(principal: Principal, shown: str, action: str) -> str:
    return (
        f"{REFUSAL_CODE}: API key {principal.key_name!r} may not {action} on {shown}; "
        "the key is limited to a repository allowlist that does not include it"
    )


def refusal_detail(request: Request, repo: str | None, *, action: str) -> str | None:
    """The 403 detail :func:`enforce_repo_allowlist` would raise, or None; no log, no audit row.

    For a caller that wants the answer without the refusal, such as a dry run.
    """
    if key_covers(request, repo):
        return None
    return _detail(request.state.principal, repo or "(no repository)", action)


async def enforce_repo_allowlist(request: Request, repo: str | None, *, action: str) -> None:
    """Refuse with 403 when the calling key may not ``action`` on ``repo``.

    ``action`` is a short verb phrase for the log and the error ("create a
    job", "resume a job"). No-op for sessions, the master key, AUTH_DEV_MODE
    and unrestricted keys.
    """
    if refusal_detail(request, repo, action=action) is not None:
        await _refuse(request, repo or "(no repository)", action)


async def enforce_owner_allowlist(request: Request, owner: str | None, *, action: str) -> None:
    """Refuse with 403, as :func:`enforce_repo_allowlist` does, when the calling key does not cover ``owner``.

    For what belongs to a forge owner rather than one repository. The refusal names ``<owner>/*``.
    """
    principal = getattr(request.state, "principal", None)
    if principal is not None and not owner_allowed(principal.allowed_repos, owner):
        await _refuse(request, f"{owner}/*" if owner else "(no owner)", action)


async def hide_unless_covered(
    request: Request, repo: str | None, missing: HTTPException, *, action: str | None = None
) -> None:
    """Answer ``missing``, what the route answers for an item that does not exist, when the calling key does not
    cover ``repo``, the repository of the one item the route reads or acts on.

    The answer is the same either way, so it never tells whether the item
    exists. A read (``action`` None) logs and audits nothing, as a list that
    leaves the item out does not. An action logs and audits the refusal as
    :func:`enforce_repo_allowlist` does, so a key that tries to act outside its
    list is still on record. No-op for sessions, the master key, AUTH_DEV_MODE
    and unrestricted keys.
    """
    if key_covers(request, repo):
        return
    await answer_as_missing(request, repo or "(no repository)", missing, action=action)


async def answer_as_missing(request: Request, shown: str, missing: HTTPException, *, action: str | None) -> NoReturn:
    """Raise ``missing`` for an item the caller's key does not cover, as :func:`hide_unless_covered` does, once the
    caller has decided it does not: an action is logged and audited naming ``shown`` (a repository, or an owner's
    ``owner/*``), a read is not."""
    if action is not None:
        await _record_refusal(request, shown, action)
    raise missing


async def _refuse(request: Request, shown: str, action: str) -> NoReturn:
    """Log, audit and raise the refusal of ``action`` on ``shown`` (a repository, or an owner's ``owner/*``)."""
    await _record_refusal(request, shown, action)
    raise HTTPException(status_code=403, detail=_detail(request.state.principal, shown, action))


async def _record_refusal(request: Request, shown: str, action: str) -> None:
    """Log and audit the refusal of ``action`` on ``shown``: ``api_key_repo_refused`` and ``apikey.repo_refused``."""
    principal = request.state.principal
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
        # Under the deployment's actor, not the key's: the key's own /audit view never holds it (GET /audit
        # leaves these rows out for a key with an allowlist). The admins see who in the details.
        actor=REPO_ALLOWLIST,
        details={
            "key_id": principal.key_id,
            "key_name": principal.key_name,
            "caller": request_actor(request),
            "repo": shown,
            "action": action,
        },
        audit_log_path=getattr(config, "audit_log_path", None),
    )
