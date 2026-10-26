"""A bare mirror per repository for the Slop-O-Meter to read its history from (spec 2026-10-26, slop-o-meter).

Cloned and fetched with the organization's forge credential, the one discovery
reads with. The credential reaches git through environment config
(``GIT_CONFIG_COUNT``), never a URL or the mirror's own config, so the mirror
on disk holds none. Every git call is a subprocess with a timeout, so a large
history never blocks the orchestrator.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import structlog

from embry0.services.slop_meter.history import LOG_ARGS
from embry0.workspace_init.git import clone_origin

logger = structlog.get_logger(__name__)

_REPO = re.compile(r"\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
#: The files a repository's head is read for: its linters' ignore lists and its areas.
Runner = Callable[[Sequence[str], dict[str, str], bytes | None, float], Awaitable[tuple[int, bytes, bytes]]]


class GitError(RuntimeError):
    """A git call failed or ran out of time; the message never holds the credential."""


async def run_git(
    args: Sequence[str], env: dict[str, str], stdin: bytes | None, timeout: float
) -> tuple[int, bytes, bytes]:
    """Run ``git`` with ``args`` in a subprocess; killed at ``timeout`` seconds."""
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(stdin), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise GitError(f"git {args[0] if args else ''} timed out after {int(timeout)}s") from None
    return process.returncode or 0, out, err


def auth_env(forge: str, token: str) -> dict[str, str]:
    """git environment config that sends ``token`` as HTTP basic auth, nothing persisted."""
    user = "oauth2" if forge == "gitlab" else "x-access-token"
    basic = base64.b64encode(f"{user}:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


class GitMirror:
    def __init__(self, root: Path, *, timeout: float = 900.0, run: Runner = run_git) -> None:
        self._root = Path(root)
        self._timeout = timeout
        self._run = run

    def path(self, repo: str) -> Path:
        if not _REPO.match(repo) or any(part in (".", "..") for part in repo.split("/")):
            raise ValueError(f"not an owner/name repository: {repo!r}")
        owner, name = repo.split("/", 1)
        return self._root / owner.lower() / f"{name.lower()}.git"

    def _env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
        env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        env.update(extra or {})
        return env

    async def _git(self, args: Sequence[str], *, auth: dict[str, str] | None = None, stdin: bytes | None = None) -> str:
        code, out, err = await self._run(args, self._env(auth), stdin, self._timeout)
        if code != 0:
            raise GitError(f"git {args[0]} exited {code}: {err.decode(errors='replace').strip()[:300]}")
        return out.decode(errors="replace")

    async def sync(self, repo: str, *, forge: str, token: str, gitlab_url: str = "") -> None:
        """Clone the repository bare when it has no mirror yet, else fetch its branches."""
        path = self.path(repo)
        auth = auth_env(forge, token) if token else None
        if not (path / "HEAD").exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"{clone_origin(forge, gitlab_url)}/{repo}.git"
            try:
                await self._git(["clone", "--bare", "--quiet", url, str(path)], auth=auth)
            except GitError:
                shutil.rmtree(path, ignore_errors=True)
                raise
            return
        await self._git(
            ["-C", str(path), "fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/heads/*"], auth=auth
        )

    async def head(self, repo: str) -> str:
        return (await self._git(["-C", str(self.path(repo)), "rev-parse", "HEAD"])).strip()

    async def history(self, repo: str) -> list[str]:
        """Every commit of the default branch's history, oldest first."""
        out = await self._git(["-C", str(self.path(repo)), "rev-list", "--reverse", "--date-order", "HEAD"])
        return [line for line in out.split() if line]

    async def log(self, repo: str, shas: Sequence[str]) -> str:
        """``git log`` text (``history.LOG_FORMAT`` with ``--numstat``) of ``shas``, in their order."""
        if not shas:
            return ""
        args = ["-C", str(self.path(repo)), *LOG_ARGS, "--no-walk=unsorted", "--stdin"]
        return await self._git(args, stdin=("\n".join(shas) + "\n").encode())

    async def read(self, repo: str, path: str) -> str | None:
        """A file of the default branch's head, or None when it has none."""
        code, out, _err = await self._run(
            ["-C", str(self.path(repo)), "show", f"HEAD:{path}"], self._env(), None, self._timeout
        )
        return out.decode(errors="replace") if code == 0 else None
