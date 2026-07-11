"""Per-owner GitHub token resolution.

The orchestrator injects a single credential per sandbox. When a repo lives
under a different GitHub owner than the default token covers (e.g. a fine-grained
PAT is single-owner), set GITHUB_TOKEN__<OWNER> in the orchestrator env. Owner is
uppercased with '-' -> '_'. Falls back to the provided default (config.github_token).
"""

from __future__ import annotations

import os


def resolve_github_token(owner: str | None, default: str) -> str:
    if not owner:
        return default
    key = "GITHUB_TOKEN__" + owner.upper().replace("-", "_")
    return os.environ.get(key) or default


def resolve_for_repo(repo: str | None, default: str) -> str:
    """Resolve the token for a repo's owner; ``default`` when repo is unset/slashless."""
    owner = repo.split("/", 1)[0] if repo and "/" in repo else None
    return resolve_github_token(owner, default)


def all_github_tokens(default: str) -> list[str]:
    """All configured GitHub tokens, ordered: default first, then per-owner
    env tokens sorted by env key. Deduped, empties dropped. Used by consumers
    that need to fan out across owners (e.g. the /github/repos listing).
    """
    tokens: list[str] = [default] if default else []
    for key in sorted(os.environ):
        if key.startswith("GITHUB_TOKEN__"):
            value = os.environ[key]
            if value and value not in tokens:
                tokens.append(value)
    return tokens
