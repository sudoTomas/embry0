"""Fill the skills store from a source (spec 3.2): git clone, directory copy, or inline rows.

The orchestrator runs git here, on a tree the admin chose and pinned, with
hooks, non-https transports and every inherited config disabled, and only
after the url's host resolved to public addresses.
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
import tempfile
from collections.abc import Awaitable, Callable
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


class SkillSyncError(Exception):
    """Why a sync failed; stored (redacted) on the row."""


class SkillSyncBusy(Exception):
    """A sync for this source is already running."""


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
    return not ip.is_global or ip.is_multicast


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
        self._locks: dict[str, asyncio.Lock] = {}

    def is_syncing(self, source_id: str) -> bool:
        lock = self._locks.get(source_id)
        return lock is not None and lock.locked()

    async def sync(self, source_id: str) -> dict[str, Any]:
        """Fill and publish one source; returns the updated row. A failure keeps the previous tree."""
        lock = self._locks.setdefault(source_id, asyncio.Lock())
        if lock.locked():
            raise SkillSyncBusy(source_id)
        async with lock:
            row = await self._repo.get(source_id)
            if row is None:
                raise ValueError(f"skill source {source_id!r} not found")
            staged: Path | None = None
            try:
                staged = self._store.staging_dir(source_id)
                sha, plugin_name, skills = await self._fill(row, staged)
                self._store.publish(source_id, sha, staged)
            except (SkillSyncError, OSError) as exc:
                if staged is not None:
                    shutil.rmtree(staged, ignore_errors=True)
                message = redact_log_text(str(exc))
                logger.warning("skill_source_sync_failed", source_id=source_id, error=message)
                return await self._repo.mark_error(source_id, message)
            except Exception as exc:
                # Anything else (a plugin.json in a foreign encoding, ...) must
                # still leave the row errored, never pending forever.
                if staged is not None:
                    shutil.rmtree(staged, ignore_errors=True)
                message = redact_log_text(f"{type(exc).__name__}: {exc}")
                logger.exception("skill_source_sync_crashed", source_id=source_id, error=message)
                return await self._repo.mark_error(source_id, message)
            updated = await self._repo.mark_synced(source_id, resolved_sha=sha, plugin_name=plugin_name, skills=skills)
            logger.info("skill_source_synced", source_id=source_id, sha=sha, skills=len(skills))
            return updated

    async def sync_missing(self) -> None:
        """Startup: every enabled source with no tree in the store. Never raises."""
        try:
            rows = await self._repo.list_all()
        except Exception:
            logger.warning("skill_source_startup_sync_failed", exc_info=True)
            return
        for row in rows:
            if not row["enabled"] or self._store.current_sha(row["id"]) is not None:
                continue
            try:
                await self.sync(row["id"])
            except Exception as exc:
                logger.warning("skill_source_startup_sync_skipped", source_id=row["id"], error=str(exc))

    async def _fill(self, row: dict[str, Any], staged: Path) -> tuple[str, str, list[dict[str, str]]]:
        kind = row["kind"]
        if kind == "git":
            sha = await self._clone(row["url"], row["ref"], staged)
        elif kind == "path":
            self._copy_dir(Path(row["url"]), staged)
        elif kind == "inline":
            await self._render_inline(staged)
        else:
            raise SkillSyncError(f"unknown source kind {kind!r}")
        _refuse_symlinks(staged)
        self._check_size(staged)
        if kind != "git":
            sha = _content_hash(staged)
        plugin_name = self._ensure_plugin_json(staged, default_name=row["id"])
        skills = _catalog(staged)
        if kind != "inline" and not skills:
            raise SkillSyncError("the tree has no skills/*/SKILL.md; a skill source must carry at least one skill")
        return sha, plugin_name, skills

    async def _check_public_host(self, url: str) -> None:
        """validate_source_url is syntactic; a public-looking name can still resolve inward."""
        host = (urlsplit(url).hostname or "").rstrip(".")
        try:
            addresses = await self._resolve_host(host)
        except OSError:
            addresses = []
        if not addresses:
            raise SkillSyncError(f"{host} does not resolve")
        if any(_is_non_public(address) for address in addresses):
            raise SkillSyncError(f"{host} resolves to a private, loopback or link-local address")

    async def _clone(self, url: str, ref: str, staged: Path) -> str:
        if url.startswith("file://"):
            if not self._allow_file:
                raise SkillSyncError("file:// urls are not accepted")
        else:
            await self._check_public_host(url)
        home = Path(tempfile.mkdtemp(prefix="embry0-skills-home-"))
        try:
            if _SHA_RE.match(ref):
                await self._git(home, staged, "init", "-q")
                await self._git(home, staged, "remote", "add", "origin", url)
                await self._git(home, staged, "fetch", "-q", "--depth", "1", "--no-recurse-submodules", "origin", ref)
                await self._git(home, staged, "checkout", "-q", "--no-recurse-submodules", "FETCH_HEAD")
            else:
                parent = staged.parent
                shutil.rmtree(staged, ignore_errors=True)
                clone = ("clone", "-q", "--depth", "1", "--no-recurse-submodules", "--branch", ref, url, staged.name)
                await self._git(home, parent, *clone)
            sha = (await self._git(home, staged, "rev-parse", "HEAD")).strip()
        finally:
            shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(staged / ".git", ignore_errors=True)
        if not _SHA_RE.match(sha):
            raise SkillSyncError(f"git did not report a commit sha for {ref!r}")
        return sha

    async def _git(self, home: Path, cwd: Path, *args: str) -> str:
        cwd.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "git",
            *self._git_safety,
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
        if not source.is_dir():
            raise SkillSyncError(f"{source} is not a directory in the orchestrator container")
        shutil.rmtree(staged, ignore_errors=True)
        # Copy links as links, never their targets; _refuse_symlinks then refuses them.
        shutil.copytree(source, staged, symlinks=True, ignore=shutil.ignore_patterns(".git"))

    async def _render_inline(self, staged: Path) -> None:
        for row in await self._inline.list_all():
            skill_dir = staged / "skills" / row["name"]
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                _render_skill_md(str(row["name"]), str(row["description"]), str(row["body"])), encoding="utf-8"
            )
        (staged / "skills").mkdir(parents=True, exist_ok=True)

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
            return name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": default_name, "version": "0.0.0"}, indent=2) + "\n", encoding="utf-8")
        return default_name


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
        out.append(
            {
                "name": name or skill_md.parent.name,
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
