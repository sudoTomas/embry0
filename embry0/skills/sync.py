"""Fill the skills store from a source (spec 3.2): git clone, directory copy, or inline rows.

The orchestrator runs git here, on a tree the admin chose and pinned, with
hooks, non-https transports and every inherited config disabled, and only
after the url's host resolved to public addresses, which git is then pinned to.
File work (copy, checks, hash, publish) runs off the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import stat
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog

from embry0.safety.redaction import redact_log_text
from embry0.skills.store import SkillStore
from embry0.storage.repositories.skills import InlineSkillsRepository, SkillSourcesRepository

logger = structlog.get_logger(__name__)

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
_PLUGIN_JSON = Path(".claude-plugin") / "plugin.json"
# The Agent SDK refuses a colon, comma, parenthesis or whitespace in either at
# connect(), and the agent sees <plugin>:<skill>; a plugin name takes no dot.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_PLUGIN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# is_global calls NAT64 global; behind it is whatever IPv4 address it embeds.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
# A startup sync that fails is retried after each of these, then left to an admin.
STARTUP_RETRY_DELAYS: tuple[float, ...] = (60.0, 300.0, 900.0)
_sleep = asyncio.sleep

_GIT_SAFETY = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "protocol.allow=never",
    "-c",
    "protocol.https.allow=always",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "submodule.recurse=false",
    # The address check vets the url's host only; a redirect would reach another.
    "-c",
    "http.followRedirects=false",
)

ResolveHost = Callable[[str], Awaitable[list[str]]]
AfterPublish = Callable[[], Awaitable[object]]


class SkillSyncError(Exception):
    """Why a sync failed; stored (redacted) on the row."""


class SkillSyncBusy(Exception):
    """A sync for this source is already running.

    ``sync`` itself never raises it (a second request is coalesced); it is
    kept for callers that refuse a request while ``is_syncing``.
    """


def parse_skill_frontmatter(text: str) -> tuple[str | None, str]:
    """(name, description) from a SKILL.md's leading ``---`` block."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return None, ""
    name: str | None = None
    description = ""
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if value.startswith('"'):
            try:
                value = str(json.loads(value))
            except json.JSONDecodeError:
                value = value.strip('"')
        else:
            value = value.strip("'\"")
        if key.strip() == "name":
            name = value or None
        elif key.strip() == "description":
            description = value
    return name, description


async def resolve_host_addresses(host: str) -> list[str]:
    """Every address ``host`` resolves to; getaddrinfo runs off the event loop."""
    loop = asyncio.get_running_loop()
    infos = await loop.run_in_executor(None, socket.getaddrinfo, host, 443, 0, socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_non_public(address: str) -> bool:
    # is_global covers private, loopback, link-local, reserved, unspecified and
    # 100.64.0.0/10; it calls some multicast ranges global, hence the second test.
    ip = ipaddress.ip_address(address)
    return not ip.is_global or ip.is_multicast or (ip.version == 6 and ip in _NAT64)


def _curl_resolve(host: str, port: int, addresses: Sequence[str]) -> str:
    """``HOST:PORT:ADDRESS[,ADDRESS]`` for http.curloptResolve; an IPv6 address goes in brackets."""
    unique = dict.fromkeys(f"[{a}]" if ":" in a else a for a in addresses)
    return f"{host}:{port}:{','.join(unique)}"


def _frontmatter_value(value: str) -> str:
    """One line, double-quoted: a JSON string is a valid YAML double-quoted scalar."""
    return json.dumps(" ".join(value.split()))


def _render_skill_md(name: str, description: str, body: str) -> str:
    return f"---\nname: {_frontmatter_value(name)}\ndescription: {_frontmatter_value(description)}\n---\n{body}"


def _git_env(home: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_ASKPASS": "/bin/false",
        "LC_ALL": "C",
    }


class SkillSourceSync:
    def __init__(
        self,
        store: SkillStore,
        repo: SkillSourcesRepository,
        inline_repo: InlineSkillsRepository,
        *,
        git_timeout: float = 120.0,
        max_tree_bytes: int = 64 * 1024 * 1024,
        allow_file_urls_for_tests: bool = False,
        resolve_host: ResolveHost | None = None,
        path_root: Path | None = None,
        after_publish: AfterPublish | None = None,
        startup_retry_delays: Sequence[float] = STARTUP_RETRY_DELAYS,
    ) -> None:
        self._store = store
        self._repo = repo
        self._inline = inline_repo
        self._git_timeout = git_timeout
        self._max_tree_bytes = max_tree_bytes
        self._allow_file = allow_file_urls_for_tests
        # protocol.allow=never refuses file:// too, so the test allowance must say so.
        self._git_safety = _GIT_SAFETY + (("-c", "protocol.file.allow=always") if allow_file_urls_for_tests else ())
        self._resolve_host = resolve_host or resolve_host_addresses
        # Where `path` sources may live; None refuses every `path` source.
        self._path_root = path_root
        # Removes the trees no sandbox mounts (SandboxManager.gc_skill_trees).
        self._after_publish = after_publish
        self._startup_retry_delays = tuple(startup_retry_delays)
        self._running: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._rerun: set[str] = set()
        # A publish moves a `current` pointer; the tree collection reads them
        # all. One at a time, so a tree never goes stale under the collection.
        self._publish_lock = asyncio.Lock()

    def is_syncing(self, source_id: str) -> bool:
        return source_id in self._running

    async def sync(self, source_id: str) -> dict[str, Any]:
        """Fill and publish one source; returns the updated row. A failure keeps the previous tree.

        A call while a sync of the same source runs is coalesced, never
        dropped: the running sync runs once more when it finishes, reading the
        row afresh (a new ref, changed inline skills), and every caller gets
        the row the last run left.
        """
        running = self._running.get(source_id)
        if running is not None:
            self._rerun.add(source_id)
            logger.info("skill_source_sync_coalesced", source_id=source_id)
            return await asyncio.shield(running)
        done: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._running[source_id] = done
        try:
            while True:
                self._rerun.discard(source_id)
                result = await self._sync_once(source_id)
                if source_id not in self._rerun:
                    break
        except asyncio.CancelledError:
            done.cancel()
            raise
        except BaseException as exc:
            done.set_exception(exc)
            done.exception()  # retrieved: the caller re-raises it; waiters may be none
            raise
        else:
            done.set_result(result)
            return result
        finally:
            del self._running[source_id]
            self._rerun.discard(source_id)

    async def _sync_once(self, source_id: str) -> dict[str, Any]:
        row = await self._repo.get(source_id)
        if row is None:
            raise ValueError(f"skill source {source_id!r} not found")
        staged: Path | None = None
        try:
            staged = await asyncio.to_thread(self._store.staging_dir, source_id)
            sha, plugin_name, skills = await self._fill(row, staged)
            await self._check_plugin_name_free(source_id, plugin_name)
            async with self._publish_lock:
                await asyncio.to_thread(self._store.publish, source_id, sha, staged)
        except (SkillSyncError, OSError) as exc:
            if staged is not None:
                await asyncio.to_thread(shutil.rmtree, staged, ignore_errors=True)
            message = redact_log_text(str(exc))
            logger.warning("skill_source_sync_failed", source_id=source_id, error=message)
            return await self._repo.mark_error(source_id, message)
        except Exception as exc:
            # Anything else (a plugin.json in a foreign encoding, ...) must
            # still leave the row errored, never pending forever.
            if staged is not None:
                await asyncio.to_thread(shutil.rmtree, staged, ignore_errors=True)
            message = redact_log_text(f"{type(exc).__name__}: {exc}")
            logger.exception("skill_source_sync_crashed", source_id=source_id, error=message)
            return await self._repo.mark_error(source_id, message)
        updated = await self._repo.mark_synced(source_id, resolved_sha=sha, plugin_name=plugin_name, skills=skills)
        logger.info("skill_source_synced", source_id=source_id, ref=row.get("ref") or None, sha=sha, skills=len(skills))
        await self._collect_garbage()
        return updated

    async def _collect_garbage(self) -> None:
        if self._after_publish is None:
            return
        async with self._publish_lock:
            try:
                await self._after_publish()
            except Exception as exc:  # noqa: BLE001 — a stale tree left behind is harmless
                logger.warning("skill_tree_gc_failed", error=str(exc))

    async def _check_plugin_name_free(self, source_id: str, plugin_name: str) -> None:
        """Two sources under one plugin name would make one skill name mean two skills."""
        for other in await self._repo.list_all():
            if other["id"] != source_id and plugin_name in (other["id"], other.get("plugin_name")):
                raise SkillSyncError(f"plugin name {plugin_name} is already used by source {other['id']}")

    async def sync_missing(self) -> None:
        """Startup: every enabled source with no tree in the store. Never raises.

        Sources still without a tree are retried after each of the startup
        retry delays, then left for an admin's ``POST .../sync``. The stale
        trees left from before the restart are collected after the first round.
        """
        pending = await self._sync_missing_once()
        await self._collect_garbage()
        for delay in self._startup_retry_delays:
            if pending == []:
                return
            logger.info("skill_source_startup_sync_retry", sources=pending, delay_seconds=delay)
            await _sleep(delay)
            pending = await self._sync_missing_once()
        if pending != []:
            logger.warning("skill_source_startup_sync_gave_up", sources=pending)

    async def _sync_missing_once(self) -> list[str] | None:
        """One round; returns the sources still without a tree, None when the registry is unreadable."""
        try:
            rows = await self._repo.list_all()
        except Exception:
            logger.warning("skill_source_startup_sync_failed", exc_info=True)
            return None
        missing = [row["id"] for row in rows if row["enabled"] and self._store.current_sha(row["id"]) is None]
        for source_id in missing:
            try:
                await self.sync(source_id)
            except Exception as exc:
                logger.warning("skill_source_startup_sync_skipped", source_id=source_id, error=str(exc))
        return [source_id for source_id in missing if self._store.current_sha(source_id) is None]

    async def _fill(self, row: dict[str, Any], staged: Path) -> tuple[str, str, list[dict[str, str]]]:
        kind = row["kind"]
        sha: str | None = None
        if kind == "git":
            sha = await self._clone(row["url"], row["ref"], staged)
        elif kind == "path":
            await asyncio.to_thread(self._copy_dir, Path(row["url"]), staged)
        elif kind == "inline":
            await self._render_inline(staged)
        else:
            raise SkillSyncError(f"unknown source kind {kind!r}")
        return await asyncio.to_thread(self._finish_tree, staged, sha=sha, kind=kind, default_name=row["id"])

    def _finish_tree(
        self, staged: Path, *, sha: str | None, kind: str, default_name: str
    ) -> tuple[str, str, list[dict[str, str]]]:
        _refuse_symlinks(staged)
        self._check_size(staged)
        if sha is None:
            sha = _content_hash(staged)
        plugin_name = self._ensure_plugin_json(staged, default_name=default_name)
        skills = _catalog(staged)
        if kind != "inline" and not skills:
            raise SkillSyncError("the tree has no skills/*/SKILL.md; a skill source must carry at least one skill")
        return sha, plugin_name, skills

    async def _check_public_host(self, url: str) -> tuple[str, list[str]]:
        """validate_source_url is syntactic; a public-looking name can still resolve inward.

        Returns the host and the addresses it was checked at, which git is then pinned to.
        """
        host = (urlsplit(url).hostname or "").rstrip(".")
        try:
            addresses = await self._resolve_host(host)
        except OSError:
            addresses = []
        if not addresses:
            raise SkillSyncError(f"{host} does not resolve")
        if any(_is_non_public(address) for address in addresses):
            raise SkillSyncError(f"{host} resolves to a private, loopback or link-local address")
        return host, addresses

    async def _clone(self, url: str, ref: str, staged: Path) -> str:
        pin: tuple[str, ...] = ()
        if url.startswith("file://"):
            if not self._allow_file:
                raise SkillSyncError("file:// urls are not accepted")
        else:
            host, addresses = await self._check_public_host(url)
            # git connects to the addresses that were checked, never to a second answer.
            pin = ("-c", f"http.curloptResolve={_curl_resolve(host, urlsplit(url).port or 443, addresses)}")
        home = Path(tempfile.mkdtemp(prefix="embry0-skills-home-"))
        try:
            if _SHA_RE.fullmatch(ref):
                await self._git(home, staged, "init", "-q")
                await self._git(home, staged, "remote", "add", "origin", url)
                await self._git(
                    home, staged, "fetch", "-q", "--depth", "1", "--no-recurse-submodules", "origin", ref, config=pin
                )
                await self._git(home, staged, "checkout", "-q", "--no-recurse-submodules", "FETCH_HEAD")
            else:
                parent = staged.parent
                await asyncio.to_thread(shutil.rmtree, staged, ignore_errors=True)
                clone = ("clone", "-q", "--depth", "1", "--no-recurse-submodules", "--branch", ref, url, staged.name)
                await self._git(home, parent, *clone, config=pin)
            sha = (await self._git(home, staged, "rev-parse", "HEAD")).strip()
        finally:
            await asyncio.to_thread(shutil.rmtree, home, ignore_errors=True)
        await asyncio.to_thread(shutil.rmtree, staged / ".git", ignore_errors=True)
        if not _SHA_RE.fullmatch(sha):
            raise SkillSyncError(f"git did not report a commit sha for {ref!r}")
        return sha

    async def _git(self, home: Path, cwd: Path, *args: str, config: tuple[str, ...] = ()) -> str:
        cwd.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "git",
            *self._git_safety,
            *config,
            *args,
            cwd=str(cwd),
            env=_git_env(home),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            # Its own process group, so a timeout reaches git's helpers too
            # (remote-https, index-pack), not only the git process.
            start_new_session=True,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self._git_timeout)
        except TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
            await proc.wait()
            raise SkillSyncError(f"git {args[0]} timed out after {self._git_timeout:g}s") from None
        if proc.returncode != 0:
            raise SkillSyncError(f"git {args[0]} failed: {err.decode('utf-8', 'replace').strip()[:500]}")
        return out.decode("utf-8", "replace")

    def _copy_dir(self, source: Path, staged: Path) -> None:
        """Copy a `path` source: confined to the path root, regular files only, counted against the cap."""
        if self._path_root is None:
            raise SkillSyncError("path sources are refused: SKILLS_PATH_ROOT is not set")
        resolved = source.resolve()
        if not resolved.is_relative_to(self._path_root.resolve()):
            raise SkillSyncError("path sources must live under SKILLS_PATH_ROOT")
        if not resolved.is_dir():
            raise SkillSyncError(f"{source} is not a directory in the orchestrator container")
        shutil.rmtree(staged, ignore_errors=True)
        copied = 0

        def copy_regular_file(src: str, dst: str) -> str:
            nonlocal copied
            st = os.lstat(src)
            if not stat.S_ISREG(st.st_mode):
                # A FIFO would block the copy; a device or socket is no skill file.
                raise SkillSyncError(
                    f"the tree contains a file that is not a regular file: {Path(src).relative_to(resolved)}"
                )
            copied += st.st_size
            if copied > self._max_tree_bytes:
                raise SkillSyncError(f"the tree is larger than the {self._max_tree_bytes} byte cap")
            shutil.copyfile(src, dst, follow_symlinks=False)
            # Readable by the sandbox's agent user; no setuid, no group or world write.
            os.chmod(dst, 0o755 if st.st_mode & stat.S_IXUSR else 0o644)
            return dst

        # Copy links as links, never their targets; _refuse_symlinks then refuses them.
        shutil.copytree(
            resolved, staged, symlinks=True, ignore=shutil.ignore_patterns(".git"), copy_function=copy_regular_file
        )
        for dirpath, _dirnames, _filenames in os.walk(staged):
            os.chmod(dirpath, 0o755)

    async def _render_inline(self, staged: Path) -> None:
        rows = await self._inline.list_all()
        await asyncio.to_thread(_write_inline, staged, rows)

    def _check_size(self, staged: Path) -> None:
        total = sum(p.stat().st_size for p in staged.rglob("*") if p.is_file())
        if total > self._max_tree_bytes:
            raise SkillSyncError(f"the tree is {total} bytes, larger than the {self._max_tree_bytes} byte cap")

    @staticmethod
    def _ensure_plugin_json(staged: Path, *, default_name: str) -> str:
        path = staged / _PLUGIN_JSON
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise SkillSyncError(f".claude-plugin/plugin.json is not JSON: {exc}") from None
            name = str(data.get("name") or "").strip() if isinstance(data, dict) else ""
            if not name:
                raise SkillSyncError(".claude-plugin/plugin.json has no name")
            if not _PLUGIN_NAME_RE.fullmatch(name):
                raise SkillSyncError(f"plugin name {name!r} must match {_PLUGIN_NAME_RE.pattern}")
            return name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": default_name, "version": "0.0.0"}, indent=2) + "\n", encoding="utf-8")
        return default_name


def _write_inline(staged: Path, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        skill_dir = staged / "skills" / row["name"]
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            _render_skill_md(str(row["name"]), str(row["description"]), str(row["body"])), encoding="utf-8"
        )
    (staged / "skills").mkdir(parents=True, exist_ok=True)


def _refuse_symlinks(staged: Path) -> None:
    """The tree is mounted into every sandbox; a link could expose anything the orchestrator can read."""
    for dirpath, dirnames, filenames in os.walk(staged, followlinks=False):
        for name in sorted(dirnames + filenames):
            path = Path(dirpath) / name
            if path.is_symlink():
                raise SkillSyncError(f"the tree contains a symlink: {path.relative_to(staged)}")


def _catalog(staged: Path) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    skills_dir = staged / "skills"
    if not skills_dir.is_dir():
        return out
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        name, description = parse_skill_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
        name = name or skill_md.parent.name
        if not _SKILL_NAME_RE.fullmatch(name):
            raise SkillSyncError(
                f"skill name {name!r} in {skill_md.relative_to(staged)} must match {_SKILL_NAME_RE.pattern}"
            )
        out.append(
            {
                "name": name,
                "description": description,
                "path": str(skill_md.relative_to(staged)),
            }
        )
    return out


def _content_hash(root: Path) -> str:
    digest = hashlib.sha1()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
