"""Per-owner GitHub token resolution.

The orchestrator injects a single credential per sandbox. When a repo lives
under a different GitHub owner than the default token covers (e.g. a fine-grained
PAT is single-owner), set GITHUB_TOKEN__<OWNER> in the orchestrator env. Owner is
uppercased with '-' -> '_'. Falls back to the provided default (config.github_token).
"""

from __future__ import annotations

import os


def env_key(owner: str) -> str:
    """The env variable that holds ``owner``'s token: ``GITHUB_TOKEN__`` and the owner uppercased, '-' as '_'.

    An env variable cannot spell '-', so the name is not one owner's alone: ``my-org`` and ``my_org`` both read
    ``GITHUB_TOKEN__MY_ORG``. A binding in the credential registry names one owner exactly (docs/credentials.md).
    """
    return "GITHUB_TOKEN__" + owner.upper().replace("-", "_")


def resolve_github_token(owner: str | None, default: str) -> str:
    if not owner:
        return default
    return os.environ.get(env_key(owner)) or default


def resolve_for_repo(repo: str | None, default: str) -> str:
    """Resolve the token for a repo's owner; ``default`` when repo is unset/slashless."""
    owner = repo.split("/", 1)[0] if repo and "/" in repo else None
    return resolve_github_token(owner, default)


def all_github_token_entries(default: str) -> list[tuple[str, str]]:
    """All configured (env_key, token) pairs, ordered: the default first,
    then per-owner env tokens sorted by env key. Deduped by token VALUE
    (first env key wins, so a per-owner var that repeats the default keeps
    the ``GITHUB_TOKEN`` label). Empties dropped.

    The default's provenance is ``config.github_token`` rather than the
    literal env var, but ``GITHUB_TOKEN`` is its honest operator-facing
    name. Env-key labels are safe to surface in API responses — token
    values never are.
    """
    entries: list[tuple[str, str]] = [("GITHUB_TOKEN", default)] if default else []
    seen: set[str] = {default} if default else set()
    for key in sorted(os.environ):
        if key.startswith("GITHUB_TOKEN__"):
            value = os.environ[key]
            if value and value not in seen:
                entries.append((key, value))
                seen.add(value)
    return entries


def all_github_tokens(default: str) -> list[str]:
    """All configured GitHub tokens, ordered: default first, then per-owner
    env tokens sorted by env key. Deduped, empties dropped. Used by consumers
    that need to fan out across owners (e.g. the /github/repos listing).
    """
    return [token for _, token in all_github_token_entries(default)]
