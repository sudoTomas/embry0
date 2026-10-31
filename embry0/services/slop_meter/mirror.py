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
import errno
import os
import re
import shutil
import stat
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Final

import structlog

from embry0.services.slop_meter.history import LOG_ARGS
from embry0.workspace_init.git import clone_origin

logger = structlog.get_logger(__name__)

_REPO = re.compile(r"\A[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
#: The files a repository's head is read for: its linters' ignore lists and its areas.
Runner = Callable[[Sequence[str], dict[str, str], bytes | None, float], Awaitable[tuple[int, bytes, bytes]]]


#: How each directory on the way to a mirror is opened for its removal: a directory, never through a link.
_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class GitError(RuntimeError):
    """A git call failed or ran out of time; the message never holds the credential."""


class MirrorRefused(RuntimeError):
    """A removal the guard refuses: it would leave the mirror root or follow a symbolic link.

    Never retried on its own: something put a link or a file where the meter
    keeps its mirrors, and a person has to look at it.
    """


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


#: The git config key every mirror the meter clones carries, so a mirror with no row is known to be its own.
MARKER: Final = "embry0.slopMeter"
#: The most of a mirror's git config read when looking for the marker.
_CONFIG_LIMIT: Final = 65536


def has_marker(config: str) -> bool:
    """Whether git config text sets :data:`MARKER` to true (``[embry0]`` then ``slopMeter = true``; a section
    and a key compared without regard to case, as git does)."""
    section, key = (part.lower() for part in MARKER.split("."))
    current = ""
    for raw in config.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
            continue
        name, _, value = line.partition("=")
        if current == section and name.strip().lower() == key and value.strip().lower() == "true":
            return True
    return False


def _entries(fd: int) -> list[str]:
    """The names in the directory open as ``fd``; nothing when it cannot be listed."""
    try:
        return sorted(os.listdir(fd))
    except OSError:
        return []


class GitMirror:
    def __init__(self, root: Path, *, timeout: float = 900.0, run: Runner = run_git) -> None:
        self._root = Path(root)
        self._timeout = timeout
        self._run = run
        #: Directories found spelled other than lowercase, each logged once.
        self._not_own: set[str] = set()

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
        """Clone the repository bare when it has no mirror yet, else fetch its branches.

        Either way the mirror carries the meter's marker (:data:`MARKER`) in its
        own git config, written at the clone, and on the next fetch of a mirror
        cloned before the marker existed: only a marked mirror with no row is
        ever removed as an orphan. Nothing is cloned, fetched or marked through
        a link: when the owner's directory or the mirror is a symbolic link or
        not a directory, :class:`MirrorRefused`, so no git writes anywhere but
        a real directory under the root.
        """
        path = self.path(repo)
        auth = auth_env(forge, token) if token else None
        if await asyncio.to_thread(self._layout, repo) == "absent" or not (path / "HEAD").exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"{clone_origin(forge, gitlab_url)}/{repo}.git"
            try:
                await self._git(["clone", "--bare", "--quiet", url, str(path)], auth=auth)
                if await asyncio.to_thread(self._layout, repo) == "mirror":
                    await self._git(["-C", str(path), "config", MARKER, "true"])
            except GitError:
                shutil.rmtree(path, ignore_errors=True)
                raise
            return
        await self._git(
            ["-C", str(path), "fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/heads/*"], auth=auth
        )
        if not await self.marked(repo) and await asyncio.to_thread(self._layout, repo) == "mirror":
            await self._git(["-C", str(path), "config", MARKER, "true"])

    def _open(self, repo: str) -> list[int] | None:
        """The root, the owner's directory and the mirror of ``repo``, each opened relative to the one above it
        without following a link: their handles, to be closed by the caller; None when one does not exist.
        :class:`MirrorRefused` when the owner's directory or the mirror is a link or not a directory."""
        path = self.path(repo)
        try:
            handles = [os.open(os.path.realpath(self._root), _DIRECTORY)]
        except OSError:
            return None
        for name, what in ((path.parent.name, "the owner's directory"), (path.name, "the mirror")):
            try:
                handles.append(os.open(name, _DIRECTORY, dir_fd=handles[-1]))
            except OSError as exc:
                for handle in handles:
                    os.close(handle)
                if exc.errno in (errno.ENOENT, errno.ENAMETOOLONG):
                    return None
                raise MirrorRefused(
                    f"{what} of {path} is a symbolic link or not a directory; a mirror is never reached through one"
                ) from None
        return handles

    def _layout(self, repo: str) -> str:
        """``mirror`` when the owner's directory and the mirror are real directories under the root, ``absent``
        when either does not exist; :class:`MirrorRefused` when either is a link or not a directory."""
        handles = self._open(repo)
        if handles is None:
            return "absent"
        for handle in handles:
            os.close(handle)
        return "mirror"

    async def marked(self, repo: str) -> bool:
        """Whether the mirror of ``repo`` carries the meter's marker in its git config. Read through handles
        opened without following a link, from a regular file only (never a link, a FIFO or a device), and
        never in a directory holding a ``.git`` entry (a checkout, not a bare mirror); False when there is no
        such mirror."""
        return await asyncio.to_thread(self._marked, repo)

    def _marked(self, repo: str) -> bool:
        try:
            handles = self._open(repo)
        except (ValueError, MirrorRefused):
            return False
        if handles is None:
            return False
        mirror_fd = handles[-1]
        try:
            try:
                os.stat(".git", dir_fd=mirror_fd, follow_symlinks=False)
                return False  # a checkout's working tree, not a bare mirror
            except FileNotFoundError:
                pass
            try:
                config_fd = os.open("config", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=mirror_fd)
            except OSError:
                return False
            with os.fdopen(config_fd, "rb") as config:
                if not stat.S_ISREG(os.fstat(config.fileno()).st_mode):
                    return False
                return has_marker(config.read(_CONFIG_LIMIT).decode(errors="replace"))
        except OSError:
            return False
        finally:
            for handle in handles:
                os.close(handle)

    async def remove(self, repo: str) -> bool:
        """Delete the mirror of ``repo``; whether there was one. Filesystem work, in a thread.

        Deletes ``<root>/<owner>/<name>.git`` and nothing else, then the
        owner's directory once it is empty. Each directory on the way is
        opened relative to the one above it without following a link, and
        the mirror is deleted through that handle, so nothing outside the
        root (the root's own real path) is ever reached. :class:`MirrorRefused`
        when the owner's directory or the mirror is a symbolic link or not a
        directory, when the root is the filesystem's root, or when this
        platform's ``shutil.rmtree`` could follow a link out of the tree. A
        link inside the mirror is removed, never followed. ``OSError`` when
        the deletion itself fails, so the caller can try again.
        """
        return await asyncio.to_thread(self._remove, repo)

    async def held(self) -> list[str]:
        """Every mirror on disk, as ``owner/name``: each directory ``<root>/<owner>/<name>.git`` whose owner's
        directory and itself are real directories, each opened relative to the one above without following a
        link. Only lowercase names, as :meth:`path` makes them: a directory spelled otherwise is not the
        meter's, and is logged once. Filesystem work, in a thread."""
        return await asyncio.to_thread(self._held)

    def _held(self) -> list[str]:
        out: list[str] = []
        try:
            root_fd = os.open(os.path.realpath(self._root), _DIRECTORY)
        except OSError:
            return out
        try:
            for owner in _entries(root_fd):
                try:
                    owner_fd = os.open(owner, _DIRECTORY, dir_fd=root_fd)
                except OSError:
                    continue  # a link, a file or something unreadable: never followed
                try:
                    for entry in _entries(owner_fd):
                        if not entry.endswith(".git"):
                            continue
                        try:
                            os.close(os.open(entry, _DIRECTORY, dir_fd=owner_fd))
                        except OSError:
                            continue
                        name = f"{owner}/{entry.removesuffix('.git')}"
                        try:
                            self.path(name)
                        except ValueError:
                            continue
                        if name != name.lower():
                            # The meter makes only lowercase paths; one spelled otherwise is not its own.
                            if name not in self._not_own:
                                self._not_own.add(name)
                                logger.warning(
                                    "slop_meter_mirror_not_lowercase",
                                    name=name,
                                    hint="the meter makes only lowercase mirror paths, so it never removes this one",
                                )
                            continue
                        out.append(name)
                finally:
                    os.close(owner_fd)
        finally:
            os.close(root_fd)
        return sorted(out)

    def _remove(self, repo: str) -> bool:
        try:
            path = self.path(repo)
        except ValueError:
            return False  # a name path() refuses was never mirrored
        root = os.path.realpath(self._root)
        if root == os.path.dirname(root):
            raise MirrorRefused("the mirror root is the filesystem's root")
        if not shutil.rmtree.avoids_symlink_attacks:
            raise MirrorRefused("this platform's rmtree can follow a symbolic link out of the mirror")
        owner, name = path.parent.name, path.name
        # Each step below the root is opened by name relative to the one above, never following a link,
        # so what is deleted is the directory found there and nothing a link points at.
        try:
            root_fd = os.open(root, _DIRECTORY)
        except FileNotFoundError:
            return False
        try:
            try:
                owner_fd = os.open(owner, _DIRECTORY, dir_fd=root_fd)
            except FileNotFoundError:
                return False
            except OSError as exc:
                if exc.errno == errno.ENAMETOOLONG:
                    return False  # no directory can have that name, so no mirror was ever made there
                if os.path.islink(os.path.join(root, owner)):
                    raise MirrorRefused(f"{path.parent} is a symbolic link; a mirror is never reached through one")
                raise MirrorRefused(f"{path.parent} is not a directory ({type(exc).__name__})") from None
            try:
                try:
                    found = os.stat(name, dir_fd=owner_fd, follow_symlinks=False)
                except FileNotFoundError:
                    return False
                except OSError as exc:
                    if exc.errno == errno.ENAMETOOLONG:
                        return False  # as above: no mirror can have that name
                    raise
                if stat.S_ISLNK(found.st_mode):
                    raise MirrorRefused(f"{path} is a symbolic link; a mirror is never reached through one")
                if not stat.S_ISDIR(found.st_mode):
                    raise MirrorRefused(f"{path} is not a directory")
                shutil.rmtree(name, dir_fd=owner_fd)
            finally:
                os.close(owner_fd)
            try:
                os.rmdir(owner, dir_fd=root_fd)  # only once no other mirror of the owner is left
            except OSError:
                pass
            return True
        finally:
            os.close(root_fd)

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
