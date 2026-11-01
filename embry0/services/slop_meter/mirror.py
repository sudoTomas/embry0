"""A bare mirror per repository for the Slop-O-Meter to read its history from (spec 2026-10-26, slop-o-meter).

Cloned and fetched with the organization's forge credential, the one discovery
reads with. The credential reaches git through environment config
(``GIT_CONFIG_COUNT``), never a URL or the mirror's own config, so the mirror
on disk holds none. Every git call is a subprocess with a timeout, so a large
history never blocks the orchestrator.

Each mirror sits one directory per segment of its repository's name, under
the mirror root and lowercased: ``owner/name`` at ``<root>/owner/name.git``,
a GitLab project in a subgroup, ``group/sub/project``, at
``<root>/group/sub/project.git`` (#298), at most :data:`DEEPEST` segments
deep. Every directory on the way is opened relative to the one above it and
never through a symbolic link, at any depth, whether the mirror is cloned
into, fetched, marked, read, listed or removed. git itself is started in the
directory the meter opened, by its handle (``/proc/self/fd/<n>`` as its
working directory), and names the mirror relative to it: ``-C .``, or the
mirror's own name for a clone into the directory above. A directory swapped
for a link after the meter opened it is never followed, by the meter or by
the git it starts. What git resolves on its own while it runs is git's: the
meter checks the layout again after a clone or a fetch, and marks nothing
through a link it finds then.
"""

from __future__ import annotations

import asyncio
import base64
import errno
import os
import re
import shutil
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Final, Protocol

import structlog

from embry0.services.slop_meter.history import LOG_ARGS
from embry0.workspace_init.git import clone_origin

logger = structlog.get_logger(__name__)

#: One segment of a repository's name, and so one directory of its mirror's path: what embry0 accepts in a
#: segment (``embry0.api.schemas.repo_path.SEGMENT``), and a leading ``_`` or ``-`` the meter has always taken.
#: ``.`` and ``..`` match it; :func:`_dirs` refuses them.
_SEGMENT = r"[A-Za-z0-9_.-]+"
#: A repository the meter mirrors: ``owner/name``, or ``group/subgroup/.../name``.
_REPO = re.compile(rf"\A{_SEGMENT}(?:/{_SEGMENT})+\Z")
#: The most segments a repository the meter mirrors has: GitLab nests at most 20 subgroups under a group, and
#: the project is the last. The listing never goes deeper either, so a deep tree, or a bind mount that loops,
#: holds at most this many directory handles open, not one per level it has.
DEEPEST: Final = 21
#: How many directories that are not mirrors one listing may walk before it says so: the meter's own layout
#: has a handful per group, so this many usually means ``SLOP_METER_DATA_PATH`` is not the meter's directory.
WALK_WARN: Final = 10_000
#: Where a process finds its own open directories by number: git is started in one of them, by its handle.
_FDS: Final = "/proc/self/fd"


class Runner(Protocol):
    """Runs ``git`` with ``args``, in the directory open as ``dir_fd`` (:func:`run_git`)."""

    async def __call__(
        self, args: Sequence[str], env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int
    ) -> tuple[int, bytes, bytes]: ...


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
    args: Sequence[str], env: dict[str, str], stdin: bytes | None, timeout: float, *, dir_fd: int
) -> tuple[int, bytes, bytes]:
    """Run ``git`` with ``args`` in a subprocess, killed at ``timeout`` seconds, its working directory the one
    open as ``dir_fd``: it starts there by the handle (``/proc/self/fd/<dir_fd>``, the handle passed to it), so
    no path is resolved again on the way, and a link swapped in since the directory was opened is not followed.
    :class:`GitError` when this system has no ``/proc/self/fd`` to start it there."""
    if not os.path.isdir(_FDS):
        raise GitError(f"git is started in the mirror's own directory through {_FDS}, which this system lacks")
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=f"{_FDS}/{dir_fd}",
        pass_fds=(dir_fd,),
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


def _dirs(repo: str) -> tuple[str, ...]:
    """The directories below the mirror root that lead to the mirror of ``repo``, the mirror last: one per
    segment, lowercased, the last as ``<name>.git`` (``group/sub/project`` is ``group``, ``sub``,
    ``project.git``).

    ValueError for a name that is no repository: fewer than two segments or more than :data:`DEEPEST`,
    ``.`` or ``..`` as one, a character no segment holds, or a segment between the first and the last that
    ends in ``.git``. That one's directory would sit where a mirror is kept (``group/sub.git`` is the mirror
    of ``group/sub``), and no forge allows such a group.
    """
    if not _REPO.match(repo):
        raise ValueError(f"not a repository path (owner/name or group/subgroup/name): {repo!r}")
    parts = repo.lower().split("/")
    if len(parts) > DEEPEST:
        raise ValueError(f"deeper than any forge nests a repository ({DEEPEST} segments at most): {repo!r}")
    if any(part in (".", "..") for part in parts):
        raise ValueError(f"not a repository path (owner/name or group/subgroup/name): {repo!r}")
    if any(part.endswith(".git") for part in parts[1:-1]):
        raise ValueError(f"a subgroup ending in .git would sit inside another repository's mirror: {repo!r}")
    return (*parts[:-1], f"{parts[-1]}.git")


def _close(handles: Sequence[int]) -> None:
    for handle in reversed(handles):
        os.close(handle)


def _is_link(name: str, dir_fd: int) -> bool:
    """Whether ``name`` in the directory open as ``dir_fd`` is a symbolic link, never following it."""
    try:
        return stat.S_ISLNK(os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


def _holds(fd: int, name: str) -> bool:
    """Whether the directory open as ``fd`` holds an entry ``name``, never following it. True when it cannot
    tell."""
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _holds_git(fd: int) -> bool:
    """Whether the directory open as ``fd`` holds a ``.git`` entry: a checkout's working tree, never a
    directory the meter made. True when it cannot tell."""
    return _holds(fd, ".git")


def _a_repository(fd: int) -> bool:
    """Whether the directory open as ``fd`` is a git repository or a checkout's working tree: it holds a
    ``.git`` entry, or a ``HEAD`` as a bare repository does. Never a directory on the way to a mirror, which
    the meter makes with lowercase names only."""
    return _holds_git(fd) or _holds(fd, "HEAD")


class GitMirror:
    def __init__(self, root: Path, *, timeout: float = 900.0, run: Runner = run_git) -> None:
        self._root = Path(root)
        self._timeout = timeout
        self._run = run
        #: Directories found spelled other than lowercase, each logged once.
        self._not_own: set[str] = set()

    def path(self, repo: str) -> Path:
        """Where the mirror of ``repo`` is kept: ``<root>/<group>/.../<name>.git``, lowercased (:func:`_dirs`)."""
        return self._root.joinpath(*_dirs(repo))

    def _env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_CONFIG_")}
        env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        env.update(extra or {})
        return env

    async def _git(
        self, args: Sequence[str], *, dir_fd: int, auth: dict[str, str] | None = None, stdin: bytes | None = None
    ) -> str:
        code, out, err = await self._run(args, self._env(auth), stdin, self._timeout, dir_fd=dir_fd)
        if code != 0:
            raise GitError(f"git {args[0]} exited {code}: {err.decode(errors='replace').strip()[:300]}")
        return out.decode(errors="replace")

    async def _in_mirror(
        self, repo: str, args: Sequence[str], *, auth: dict[str, str] | None = None, stdin: bytes | None = None
    ) -> str:
        """Run ``git -C . <args>`` in the mirror of ``repo``, started in the directory :meth:`_open` opened.
        :class:`GitError` when there is no mirror; :class:`MirrorRefused` when the way to it is a link."""
        handles = await asyncio.to_thread(self._open, repo)
        if handles is None:
            raise GitError(f"git {args[0]}: {repo} has no mirror")
        try:
            return await self._git(["-C", ".", *args], dir_fd=handles[-1], auth=auth, stdin=stdin)
        finally:
            _close(handles)

    async def sync(self, repo: str, *, forge: str, token: str, gitlab_url: str = "") -> None:
        """Clone the repository bare when it has no mirror yet, else fetch its branches.

        Either way the mirror carries the meter's marker (:data:`MARKER`) in its
        own git config, written at the clone, and on the next fetch of a mirror
        cloned before the marker existed: only a marked mirror with no row is
        ever removed as an orphan.

        Nothing is cloned, fetched or marked through a link. When a directory
        on the way to the mirror (its owner's, each subgroup's) or the mirror
        itself is a symbolic link or not a directory, :class:`MirrorRefused`.
        Otherwise git is started in the directory found: the mirror's for a
        fetch and the marker (``git -C .``), the one above it for a clone into
        the mirror's name. So a directory swapped for a link after that check
        is never followed: git works in the directory that was checked,
        wherever it has been moved. After a clone or a fetch the layout is
        checked again, and the marker is written only when it still holds. The
        directories above a new mirror are made one at a time, each relative
        to the one above it; what a failed clone leaves is removed as a prune
        removes a mirror, never through a link.
        """
        *_parents, name = _dirs(repo)
        auth = auth_env(forge, token) if token else None
        handles = await asyncio.to_thread(self._open, repo)
        if handles is not None and _holds(handles[-1], "HEAD"):
            try:
                await self._git(
                    ["-C", ".", "fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/heads/*"],
                    dir_fd=handles[-1],
                    auth=auth,
                )
            finally:
                _close(handles)
            if not await self.marked(repo):
                await self._mark(repo)
            return
        if handles is not None:
            _close(handles)  # a directory without HEAD: cloned into again, which fails unless it is empty
        above = await asyncio.to_thread(self._make_parents, repo)
        url = f"{clone_origin(forge, gitlab_url)}/{repo}.git"
        try:
            try:
                await self._git(["clone", "--bare", "--quiet", url, name], dir_fd=above[-1], auth=auth)
            finally:
                _close(above)
            await self._mark(repo)
        except GitError:
            await asyncio.to_thread(self._discard, repo)
            raise

    async def _mark(self, repo: str) -> None:
        """Write the meter's marker into the mirror of ``repo``, git started in the directory the layout check
        opened: :class:`MirrorRefused` when the way to it is now a link; nothing when it is gone."""
        handles = await asyncio.to_thread(self._open, repo)
        if handles is None:
            return
        try:
            await self._git(["-C", ".", "config", MARKER, "true"], dir_fd=handles[-1])
        finally:
            _close(handles)

    def _open(self, repo: str) -> list[int] | None:
        """The root, then each directory on the way to the mirror of ``repo`` (its owner's, each subgroup's),
        then the mirror, each opened relative to the one above it without following a link: their handles, to
        be closed by the caller; None when one does not exist. :class:`MirrorRefused` when one below the root
        is a link or not a directory."""
        dirs = _dirs(repo)
        try:
            handles = [os.open(os.path.realpath(self._root), _DIRECTORY)]
        except OSError:
            return None
        for depth, name in enumerate(dirs, start=1):
            try:
                handles.append(os.open(name, _DIRECTORY, dir_fd=handles[-1]))
            except OSError as exc:
                _close(handles)
                if exc.errno in (errno.ENOENT, errno.ENAMETOOLONG):
                    return None
                raise MirrorRefused(
                    f"{self._root.joinpath(*dirs[:depth])}, on the way to the mirror of {repo}, is a symbolic link "
                    "or not a directory; a mirror is never reached through one"
                ) from None
        return handles

    def _make_parents(self, repo: str) -> list[int]:
        """Make each directory above the mirror of ``repo`` that does not exist yet, each made and opened
        relative to the one above it, never through a link: the root's handle and each of theirs, the one the
        mirror goes in last, to be closed by the caller. :class:`MirrorRefused` when one is a link or not a
        directory."""
        *parents, _mirror = _dirs(repo)
        self._root.mkdir(parents=True, exist_ok=True)
        handles = [os.open(os.path.realpath(self._root), _DIRECTORY)]
        try:
            for depth, name in enumerate(parents, start=1):
                try:
                    os.mkdir(name, dir_fd=handles[-1])
                except FileExistsError:
                    pass  # made before, or something else is there: opened below without following it
                try:
                    handles.append(os.open(name, _DIRECTORY, dir_fd=handles[-1]))
                except OSError:
                    raise MirrorRefused(
                        f"{self._root.joinpath(*parents[:depth])}, on the way to the mirror of {repo}, is a symbolic "
                        "link or not a directory; a mirror is never reached through one"
                    ) from None
        except BaseException:
            _close(handles)
            raise
        return handles

    def _discard(self, repo: str) -> None:
        """Remove what a failed clone left of the mirror of ``repo``, as :meth:`remove` does: never through a
        link. Left in place when the guard refuses or the deletion fails; the next sync finds it."""
        try:
            self._remove(repo)
        except (OSError, MirrorRefused):
            pass

    async def marked(self, repo: str) -> bool:
        """Whether the mirror of ``repo`` carries the meter's marker in its git config. Read through handles
        opened without following a link, from a regular file only (never a link, a FIFO or a device), and
        never when the mirror or a directory on the way to it holds a ``.git`` entry (a checkout, or a
        directory inside one, not a bare mirror); False when there is no such mirror."""
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
            if any(_holds_git(handle) for handle in handles[1:]):
                return False  # a checkout's working tree, or a directory inside one: not a bare mirror
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
            _close(handles)

    async def remove(self, repo: str) -> bool:
        """Delete the mirror of ``repo``; whether there was one. Filesystem work, in a thread.

        Deletes ``<root>/<group>/.../<name>.git`` and nothing else, then each
        directory above it that is left empty, deepest first, up to and never
        including the root: another mirror's directories, a sibling
        subgroup's, stay. Each directory on the way is opened relative to the
        one above it without following a link, and the mirror is deleted
        through that handle, so nothing outside the root (the root's own real
        path) is ever reached; an empty directory is removed by its name in
        the directory above, which removes a real empty directory and never a
        link. :class:`MirrorRefused` when a directory on the way (the owner's,
        a subgroup's) or the mirror is a symbolic link or not a directory,
        when the root is the filesystem's root, or when this platform's
        ``shutil.rmtree`` could follow a link out of the tree. A link inside
        the mirror is removed, never followed. ``OSError`` when the deletion
        itself fails, so the caller can try again.
        """
        return await asyncio.to_thread(self._remove, repo)

    async def held(self) -> list[str]:
        """Every mirror on disk, as ``owner/name`` or ``group/sub/.../name``: each real directory
        ``<root>/<group>/.../<name>.git`` two to :data:`DEEPEST` levels below the root, reached through real
        directories only, each opened relative to the one above without following a link. A directory on the
        way is never itself a mirror, and nothing inside a mirror, a checkout (a ``.git`` entry) or a bare
        repository (a ``HEAD``) is looked at, nor anything deeper than a mirror can be: the walk holds at most
        :data:`DEEPEST` handles open, however deep the tree. Only lowercase names, as :meth:`path` makes them:
        a directory spelled otherwise is not the meter's, and is logged once. A walk through more than
        :data:`WALK_WARN` directories that are not mirrors is logged (``slop_meter_mirror_walk_large``): the
        root is likely not the meter's. Filesystem work, in a thread."""
        return await asyncio.to_thread(self._held)

    def _held(self) -> list[str]:
        out: list[str] = []
        try:
            root_fd = os.open(os.path.realpath(self._root), _DIRECTORY)
        except OSError:
            return out
        # Depth first, without recursion: each open directory, the names on the way to it, and its entries
        # still to look at. Each directory is opened relative to the one above it, never through a link.
        stack: list[tuple[int, tuple[str, ...], list[str]]] = [(root_fd, (), _entries(root_fd)[::-1])]
        walked = 0
        try:
            while stack:
                fd, above, todo = stack[-1]
                if not todo:
                    stack.pop()
                    os.close(fd)
                    continue
                entry = todo.pop()
                if above and entry.endswith(".git"):
                    # Two or more levels down, a mirror's place: listed, never looked inside.
                    self._list(fd, (*above, entry), out)
                    continue
                if len(above) + 1 >= DEEPEST:
                    continue  # a directory this deep holds no mirror the meter makes: not opened
                try:
                    child = os.open(entry, _DIRECTORY, dir_fd=fd)
                except OSError:
                    continue  # a link, a file or something unreadable: never followed
                walked += 1
                if _a_repository(child):
                    os.close(child)  # a checkout or a bare repository: nothing in it is the meter's
                    continue
                stack.append((child, (*above, entry), _entries(child)[::-1]))
        finally:
            _close([fd for fd, _above, _todo in stack])
        if walked > WALK_WARN:
            logger.warning(
                "slop_meter_mirror_walk_large",
                root=str(self._root),
                directories=walked,
                mirrors=len(out),
                hint="the listing walked more directories that are not mirrors than the meter's own layout has; "
                "SLOP_METER_DATA_PATH is likely not a directory of the meter's own",
            )
        return sorted(out)

    def _list(self, fd: int, parts: tuple[str, ...], out: list[str]) -> None:
        """Add the mirror ``parts[-1]`` in the directory open as ``fd`` to ``out`` when it is a real directory
        and its name is one :meth:`path` makes."""
        try:
            os.close(os.open(parts[-1], _DIRECTORY, dir_fd=fd))
        except OSError:
            return
        name = "/".join((*parts[:-1], parts[-1].removesuffix(".git")))
        try:
            _dirs(name)
        except ValueError:
            return
        if name != name.lower():
            # The meter makes only lowercase paths; one spelled otherwise is not its own.
            if name not in self._not_own:
                self._not_own.add(name)
                logger.warning(
                    "slop_meter_mirror_not_lowercase",
                    name=name,
                    hint="the meter makes only lowercase mirror paths, so it never removes this one",
                )
            return
        out.append(name)

    def _remove(self, repo: str) -> bool:
        try:
            dirs = _dirs(repo)
        except ValueError:
            return False  # a name path() refuses was never mirrored
        root = os.path.realpath(self._root)
        if root == os.path.dirname(root):
            raise MirrorRefused("the mirror root is the filesystem's root")
        if not shutil.rmtree.avoids_symlink_attacks:
            raise MirrorRefused("this platform's rmtree can follow a symbolic link out of the mirror")
        *parents, name = dirs
        path = self._root.joinpath(*dirs)
        # Each step below the root is opened by name relative to the one above, never following a link,
        # so what is deleted is the directory found there and nothing a link points at.
        try:
            handles = [os.open(root, _DIRECTORY)]
        except FileNotFoundError:
            return False
        try:
            for depth, parent in enumerate(parents, start=1):
                try:
                    handles.append(os.open(parent, _DIRECTORY, dir_fd=handles[-1]))
                except FileNotFoundError:
                    return False
                except OSError as exc:
                    if exc.errno == errno.ENAMETOOLONG:
                        return False  # no directory can have that name, so no mirror was ever made there
                    where = self._root.joinpath(*parents[:depth])
                    if _is_link(parent, handles[-1]):
                        raise MirrorRefused(
                            f"{where} is a symbolic link; a mirror is never reached through one"
                        ) from None
                    raise MirrorRefused(f"{where} is not a directory ({type(exc).__name__})") from None
            try:
                found = os.stat(name, dir_fd=handles[-1], follow_symlinks=False)
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
            shutil.rmtree(name, dir_fd=handles[-1])
            # Then each directory above it left empty, deepest first, never the root: removed by its name in the
            # directory above, which removes only a real, empty directory (never a link, never one holding
            # another mirror or a sibling subgroup's).
            for depth in range(len(parents), 0, -1):
                try:
                    os.rmdir(parents[depth - 1], dir_fd=handles[depth - 1])
                except OSError:
                    break
            return True
        finally:
            _close(handles)

    async def head(self, repo: str) -> str:
        return (await self._in_mirror(repo, ["rev-parse", "HEAD"])).strip()

    async def history(self, repo: str) -> list[str]:
        """Every commit of the default branch's history, oldest first."""
        out = await self._in_mirror(repo, ["rev-list", "--reverse", "--date-order", "HEAD"])
        return [line for line in out.split() if line]

    async def log(self, repo: str, shas: Sequence[str]) -> str:
        """``git log`` text (``history.LOG_FORMAT`` with ``--numstat``) of ``shas``, in their order."""
        if not shas:
            return ""
        args = [*LOG_ARGS, "--no-walk=unsorted", "--stdin"]
        return await self._in_mirror(repo, args, stdin=("\n".join(shas) + "\n").encode())

    async def read(self, repo: str, path: str) -> str | None:
        """A file of the default branch's head, or None when it has none."""
        try:
            return await self._in_mirror(repo, ["show", f"HEAD:{path}"])
        except GitError:
            return None
