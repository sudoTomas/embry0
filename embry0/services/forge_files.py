"""One file of a repository's default branch, read through a forge client (GitHub's contents API).

embry0 reads no configuration of its own from a target repository (spec
2026-11-10, #175). What it reads this way are files a repository keeps for
its own reasons, as evidence: its ``CODEOWNERS``, the owners fallback of owner
proposals.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import Any


async def read_contents(get: Callable[[str], Awaitable[Any]], repo: str, path: str) -> str | None:
    """A file of the default branch through a forge client's GET; None when it is not there or cannot be read."""
    resp = await get(f"/repos/{repo}/contents/{path}")
    if resp.status_code != 200:
        return None
    body = resp.json()
    if not isinstance(body, dict) or not isinstance(body.get("content"), str):
        return None
    return base64.b64decode(body["content"]).decode("utf-8", "replace")
