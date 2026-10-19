"""The skills store: /skills/<source_id>/<resolved_sha>/ trees and a `current` pointer (spec 3.2).

The orchestrator writes here; the sandbox daemon bind-mounts a tree read-only
into each sandbox at /opt/embry0/skills/<id>. DinD sees the same volume at the
same path; a daemon that sees the store elsewhere (the rootless daemon, on the
host) is given that path as ``daemon_root``.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

CURRENT = "current"
STAGING = ".staging"


@dataclass(frozen=True)
class SkillMount:
    source_id: str
    plugin_name: str
    host_path: str  # <daemon_root>/<id>/<sha>, the path the sandbox daemon bind-mounts
    container_path: str  # /opt/embry0/skills/<id>


class SkillStore:
    SANDBOX_ROOT = "/opt/embry0/skills"

    def __init__(self, root: Path, *, daemon_root: Path | None = None) -> None:
        self._root = Path(root)
        # Where the sandbox daemon sees ``root``: bind sources are resolved in
        # its namespace, not the orchestrator's. None means the same path.
        self._daemon_root = Path(daemon_root) if daemon_root is not None else self._root

    @property
    def root(self) -> Path:
        return self._root

    def source_dir(self, source_id: str) -> Path:
        return self._root / source_id

    def tree_path(self, source_id: str, sha: str) -> Path:
        return self.source_dir(source_id) / sha

    def staging_dir(self, source_id: str) -> Path:
        """An empty sibling of the final tree, so the publish rename stays on one filesystem."""
        path = self.source_dir(source_id) / STAGING
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
        return path

    def current_sha(self, source_id: str) -> str | None:
        pointer = self.source_dir(source_id) / CURRENT
        try:
            sha = pointer.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        return sha or None

    def daemon_path(self, path: Path) -> str:
        """``path`` (a path under ``root``) as the sandbox daemon sees it."""
        return str(self._daemon_root / Path(path).relative_to(self._root))

    def publish(self, source_id: str, sha: str, staged: Path) -> Path:
        """Rename ``staged`` into place and point ``current`` at it.

        Older trees stay: a running or paused sandbox may still bind-mount
        one. ``stale_trees`` lists them for a caller that knows which trees
        are mounted (``SandboxManager.gc_skill_trees``).
        """
        target = self.tree_path(source_id, sha)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            # The same content is already live and may be bind-mounted into
            # running sandboxes: keep it, drop the redundant copy.
            shutil.rmtree(staged, ignore_errors=True)
        else:
            Path(staged).rename(target)
        pointer = self.source_dir(source_id) / CURRENT
        tmp = pointer.with_suffix(".tmp")
        tmp.write_text(sha + "\n", encoding="utf-8")
        tmp.replace(pointer)
        logger.info("skill_source_published", source_id=source_id, sha=sha)
        return target

    def stale_trees(self, source_id: str) -> list[Path]:
        """Every tree of the source except the current one (and the staging directory).

        None without a ``current`` pointer: with nothing known to be current,
        nothing is known to be stale (and a directory that is no source keeps
        its contents).
        """
        current = self.current_sha(source_id)
        source_dir = self.source_dir(source_id)
        if current is None or not source_dir.is_dir():
            return []
        keep = {current, STAGING}
        return sorted(entry for entry in source_dir.iterdir() if entry.is_dir() and entry.name not in keep)

    def all_stale_trees(self) -> list[Path]:
        """``stale_trees`` of every source directory in the store."""
        if not self._root.is_dir():
            return []
        return [
            tree for entry in sorted(self._root.iterdir()) if entry.is_dir() for tree in self.stale_trees(entry.name)
        ]

    def remove_tree(self, path: Path) -> None:
        """Remove one tree; never the current one, which a new sandbox may be about to mount."""
        path = Path(path)
        if path.parent.parent != self._root or path.name in {CURRENT, STAGING}:
            raise ValueError(f"{path} is not a tree of this store")
        if self.current_sha(path.parent.name) == path.name:
            raise ValueError(f"{path} is the current tree of {path.parent.name}")
        shutil.rmtree(path, ignore_errors=True)
        logger.info("skill_tree_removed", source_id=path.parent.name, sha=path.name)

    def mounts(self, sources: Iterable[Mapping[str, Any]]) -> list[SkillMount]:
        """One mount per enabled source whose current tree is on disk.

        ``status`` is informational: a failed re-sync keeps the previous tree,
        and that tree stays mounted.
        """
        out: list[SkillMount] = []
        for src in sources:
            if not src.get("enabled"):
                continue
            source_id = str(src["id"])
            sha = self.current_sha(source_id)
            if sha is None or not self.tree_path(source_id, sha).is_dir():
                logger.warning("skill_source_mount_missing", source_id=source_id, sha=sha)
                continue
            out.append(
                SkillMount(
                    source_id=source_id,
                    plugin_name=str(src.get("plugin_name") or source_id),
                    host_path=str(self._daemon_root / source_id / sha),
                    container_path=f"{self.SANDBOX_ROOT}/{source_id}",
                )
            )
        return out

    def remove_source(self, source_id: str) -> None:
        shutil.rmtree(self.source_dir(source_id), ignore_errors=True)
