"""Per-owner GitHub token resolution (B2 / INT-654).

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
