"""The skills store: /skills/<source_id>/<resolved_sha>/ trees and a `current` pointer (spec 3.2).

The orchestrator writes here; DinD sees the same volume at the same path and
bind-mounts a tree read-only into each sandbox at /opt/embry0/skills/<id>.
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
    host_path: str  # /skills/<id>/<sha>, the path DinD bind-mounts
    container_path: str  # /opt/embry0/skills/<id>


class SkillStore:
    SANDBOX_ROOT = "/opt/embry0/skills"

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

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

    def publish(self, source_id: str, sha: str, staged: Path) -> Path:
        """Rename ``staged`` into place, point ``current`` at it, drop older trees."""
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
        for entry in self.source_dir(source_id).iterdir():
            if entry.is_dir() and entry.name not in {sha, STAGING}:
                shutil.rmtree(entry, ignore_errors=True)
        logger.info("skill_source_published", source_id=source_id, sha=sha)
        return target

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
                    host_path=str(self.tree_path(source_id, sha)),
                    container_path=f"{self.SANDBOX_ROOT}/{source_id}",
                )
            )
        return out

    def remove_source(self, source_id: str) -> None:
        shutil.rmtree(self.source_dir(source_id), ignore_errors=True)
