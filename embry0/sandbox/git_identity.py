"""Sandbox git identity resolution (EMB-51).

One resolver for every pipeline path that configures git inside a sandbox;
:func:`embry0.execution.sandbox_git.configure` writes what it resolves.
Precedence per field: repo_preferences override → bound forge account's
author → branding default (which is itself env-overridable via
EMBRY0_GIT_AUTHOR_NAME / EMBRY0_GIT_AUTHOR_EMAIL).

Before EMB-51 the QA path hardcoded ``qa-agent@embry0.local`` — a
non-routable address GitHub cannot associate with any account, which org
rulesets reject on push. All paths now flow through this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from embry0.branding import GIT_AUTHOR_EMAIL, GIT_AUTHOR_NAME

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class GitIdentity:
    name: str
    email: str


def default_git_identity() -> GitIdentity:
    """Branding identity — env-overridable, valid noreply address by default."""
    return GitIdentity(name=GIT_AUTHOR_NAME, email=GIT_AUTHOR_EMAIL)


def _nonblank(value: Any) -> str | None:
    """``value`` when it is a non-blank string, else None.

    A malformed row, a credential without an author, or a MagicMock stand-in
    from a test must degrade to the next source, never into the git config.
    """
    return value if isinstance(value, str) and value.strip() else None


async def _preferred_author(prefs_repo: Any | None, repo: str) -> tuple[str | None, str | None]:
    """(name, email) pinned in repo_preferences; (None, None) when unset or unreadable."""
    if prefs_repo is None or not repo:
        return None, None
    try:
        pref = await prefs_repo.get(repo)
    except Exception:
        logger.warning("git_identity_prefs_fetch_failed", repo=repo, exc_info=True)
        return None, None
    if not pref:
        return None, None
    return _nonblank(pref.get("git_author_name")), _nonblank(pref.get("git_author_email"))


async def resolve_git_identity(prefs_repo: Any | None, repo: str, *, credential: Any | None = None) -> GitIdentity:
    """Effective identity for ``repo``: per-repo preference, else the bound
    account's author (spec 2026-09-29 §3.3), else the branding default.

    Each field overrides independently (a repo may pin just the email).
    Fetch failures fall back — identity resolution must never fail a job.
    """
    base = default_git_identity()
    pref_name, pref_email = await _preferred_author(prefs_repo, repo)
    cred_name = _nonblank(getattr(credential, "git_author_name", None))
    cred_email = _nonblank(getattr(credential, "git_author_email", None))
    identity = GitIdentity(
        name=pref_name or cred_name or base.name,
        email=pref_email or cred_email or base.email,
    )
    if pref_name or pref_email:
        logger.info("git_identity_overridden_by_prefs", repo=repo, name=identity.name, email=identity.email)
    # Logged only when a credential field actually survived the preference.
    if (cred_name and not pref_name) or (cred_email and not pref_email):
        logger.info("git_identity_from_credential", repo=repo, label=getattr(credential, "label", None))
    return identity
