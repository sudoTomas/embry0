"""One file of a repository's default branch, read through a forge client (GitHub's contents API, GitLab's raw
file).

embry0 reads no configuration of its own from a target repository (spec
2026-11-10, #175). What it reads this way are files a repository keeps for
its own reasons, as evidence: its ``CODEOWNERS``, the owners fallback of owner
proposals.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote


async def read_contents(get: Callable[[str], Awaitable[Any]], repo: str, path: str) -> str | None:
    """A file of the default branch through a forge client's GET, its text; None when it is not there. Raises as
    :func:`read_file` does when it could not be read: "could not read" is not "no file"."""
    found = await read_file(get, repo, path)
    return None if found is None else found[0]


async def read_gitlab_file(client: Any, repo: str, path: str) -> str | None:
    """A file of a GitLab project's default branch through a ``GitLabClient`` (the repository files API's raw read);
    None when it is not there. Raises what the client raises, and :class:`ForgeReadFailed` for any other answer, so a
    caller can tell "no file" from "could not read".

    The project is read first, for its default branch: one that does not read (GitLab answers 404 for a project the
    credential cannot see, as for one that does not exist) raises, so a 404 for the file is "not there" only when
    the project itself reads, as :func:`read_file` has it on GitHub. A project that reads with no default branch
    (nothing pushed yet) has no file."""
    project = await client.get_project(repo)
    if not isinstance(project, dict):
        raise ForgeReadFailed("the forge does not show the project to embry0's credential")
    branch = project.get("default_branch")
    if branch is None or branch == "":
        return None
    if not isinstance(branch, str):
        raise ForgeReadFailed("the project's default branch could not be read")
    resp = await client.get(
        client.project_path(repo, f"/repository/files/{quote(path, safe='')}/raw"), repo=repo, params={"ref": branch}
    )
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ForgeReadFailed(f"the forge answered {resp.status_code}")
    return bytes(resp.content).decode("utf-8", "replace")


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
