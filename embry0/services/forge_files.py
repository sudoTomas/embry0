"""One file of a repository's default branch, read through a forge client (GitHub's contents API).

embry0 reads no configuration of its own from a target repository (spec
2026-11-10). What it reads this way are files a repository keeps for its
own reasons, as evidence: its ``CODEOWNERS``, the owners fallback of owner
proposals. The one-time import of a repository's old areas file reads it
here too, until the release that removes the import.
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


async def read_file(get: Callable[[str], Awaitable[Any]], repo: str, path: str) -> tuple[str, str | None] | None:
    """A file of the default branch and its blob sha through a forge client's GET; None when it is not there.

    Raises what the client raises (a network failure), and :class:`ForgeReadFailed` for an answer that is
    neither the file nor "not there", so a caller can tell "no file" from "could not read". GitHub answers
    404 for a private repository the credential cannot see, as for a file that is not there: a 404 is "not
    there" only when the repository itself reads, and otherwise :class:`ForgeReadFailed`.
    """
    resp = await get(f"/repos/{repo}/contents/{path}")
    if resp.status_code == 404:
        shown = await get(f"/repos/{repo}")
        if shown.status_code == 200:
            return None
        raise ForgeReadFailed(f"the forge does not show the repository to embry0's credential ({shown.status_code})")
    if resp.status_code != 200:
        raise ForgeReadFailed(f"the forge answered {resp.status_code}")
    body = resp.json()
    if not isinstance(body, dict) or not isinstance(body.get("content"), str):
        return None
    sha = body.get("sha")
    return base64.b64decode(body["content"]).decode("utf-8", "replace"), sha if isinstance(sha, str) else None


class ForgeReadFailed(RuntimeError):
    """The forge answered neither with the file nor that it is not there."""
