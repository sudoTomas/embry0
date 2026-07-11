"""SHA-256 of the repo's lockfile — drives image rebuild decisions.

Order of preference: package-lock.json > pnpm-lock.yaml > yarn.lock.
The hash is a stable identifier for "this dependency set". Two runs with
the same lockfile produce the same SHA → same image tag is reusable.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_LOCKFILE_CANDIDATES: tuple[str, ...] = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
)


class LockfileNotFoundError(FileNotFoundError):
    """No recognized lockfile in the given workspace root."""


def compute_lockfile_sha(workspace_root: Path) -> str:
    """Return the SHA-256 hex digest of the first found lockfile.

    The first candidate in `_LOCKFILE_CANDIDATES` order wins. Raises
    LockfileNotFoundError if none are found.
    """
    for candidate in _LOCKFILE_CANDIDATES:
        path = Path(workspace_root) / candidate
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    raise LockfileNotFoundError(
        f"no lockfile found in {workspace_root} (looked for: {', '.join(_LOCKFILE_CANDIDATES)})"
    )
