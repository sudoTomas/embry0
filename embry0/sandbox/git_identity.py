"""Sandbox git identity resolution (EMB-51).

One resolver for every pipeline path that configures git inside a sandbox.
Precedence per field: repo_preferences override → branding default (which is
itself env-overridable via EMBRY0_GIT_AUTHOR_NAME / EMBRY0_GIT_AUTHOR_EMAIL).

Before EMB-51 the QA path hardcoded ``qa-agent@embry0.local`` — a
non-routable address GitHub cannot associate with any account, which org
rulesets reject on push. All paths now flow through this module.
"""

from __future__ import annotations

import shlex
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


async def resolve_git_identity(prefs_repo: Any | None, repo: str) -> GitIdentity:
    """Effective identity for ``repo``: per-repo override, else branding default.

    Each field overrides independently (a repo may pin just the email).
    Fetch failures fall back to the default — identity resolution must never
    fail a job.
    """
    identity = default_git_identity()
    if prefs_repo is None or not repo:
        return identity
    try:
        pref = await prefs_repo.get(repo)
    except Exception:
        logger.warning("git_identity_prefs_fetch_failed", repo=repo, exc_info=True)
        return identity
    if not pref:
        return identity
    # Only non-empty strings override — a malformed row (or a non-dict-like
    # stub in tests) must degrade to the default, never into the git config.
    raw_name = pref.get("git_author_name")
    raw_email = pref.get("git_author_email")
    name = raw_name if isinstance(raw_name, str) and raw_name.strip() else identity.name
    email = raw_email if isinstance(raw_email, str) and raw_email.strip() else identity.email
    if (name, email) != (identity.name, identity.email):
        logger.info("git_identity_overridden_by_prefs", repo=repo, name=name, email=email)
    return GitIdentity(name=name, email=email)


def build_git_identity_cmd(identity: GitIdentity) -> str:
    """Shell fragment setting the sandbox-global git identity."""
    return (
        f"git config --global user.email {shlex.quote(identity.email)} && "
        f"git config --global user.name {shlex.quote(identity.name)}"
    )
