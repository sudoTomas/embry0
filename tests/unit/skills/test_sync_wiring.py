"""The wiring pin: an optional argument production never passes fails silently (#369)."""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import embry0.api.app as app_module


def _construction(src: str, pattern: str) -> str:
    """The text of the one call matching ``pattern``, up to its matching close paren."""
    [match] = list(re.finditer(pattern, src))
    depth, i = 0, match.end() - 1
    while True:
        depth += src[i] == "("
        depth -= src[i] == ")"
        if depth == 0:
            return src[match.start() : i + 1]
        i += 1


def test_production_gives_the_skill_sync_its_tree_collection_and_path_root() -> None:
    """Without after_publish every re-sync leaves its older tree on disk, forever."""
    src = Path(inspect.getsourcefile(app_module) or "").read_text(encoding="utf-8")
    call = _construction(src, r"app\.state\.skill_sync = SkillSourceSync\(")
    assert "after_publish=sandbox_mgr.gc_skill_trees" in call, call
    assert "path_root=config.skills_path_root" in call, call
    assert src.index("sandbox_mgr = SandboxManager(") < src.index("app.state.skill_sync = SkillSourceSync("), (
        "the sync is built before the manager whose collection it calls"
    )
