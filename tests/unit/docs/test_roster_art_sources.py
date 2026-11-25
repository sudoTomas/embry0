"""Every image the repository ships says what licence it ships under, and where its record is (#178).

Which roster shapes the code draws, and that each has one provenance row in ``appearance/SOURCES.md``, is held by the
frontend's own test (``frontend/src/components/roster/appearance/__tests__/shapes.test.ts``), which reads the shapes
from the code's registry rather than from its text.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
APPEARANCE_SOURCES = REPO / "frontend" / "src" / "components" / "roster" / "appearance" / "SOURCES.md"
WORLD_SOURCES = REPO / "frontend" / "public" / "world" / "SOURCES.md"
NOTICES = REPO / "THIRD_PARTY_NOTICES.md"

#: What an image file is, by its extension.
IMAGE = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|bmp|avif|tiff?)$", re.IGNORECASE)
#: Images drawn in code as text grids, which no extension finds: each is named in the notices too.
DRAWN_IN_CODE = (
    "frontend/src/components/roster/appearance/",
    "frontend/src/components/world/CreatureIcon.tsx",
    "frontend/src/components/progression/achievementIconData.ts",
)
#: Never the repository's own files, when it is walked rather than listed (a tree with no .git, as `git archive`
#: leaves it).
_NOT_TRACKED = {".git", "node_modules", ".venv", "dist", "build", "__pycache__", ".mypy_cache", ".ruff_cache"}


def _files() -> list[str]:
    """The repository's files: what git tracks, or every file of a tree with no git."""
    listed = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, check=False)
    if listed.returncode == 0 and listed.stdout:
        return [name for name in listed.stdout.decode().split("\0") if name]
    found: list[str] = []
    for root, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in _NOT_TRACKED and not d.startswith(".pytest")]
        found += [str((Path(root) / name).relative_to(REPO)) for name in names]
    return found


def _images_section() -> str:
    notices = NOTICES.read_text()
    assert "\n## Images\n" in notices, "THIRD_PARTY_NOTICES.md has no Images section"
    return notices.split("\n## Images\n", 1)[1].split("\n## ", 1)[0]


def _licence(path: Path) -> str:
    assert path.exists(), f"{path.relative_to(REPO)} is missing"
    line = next((text for text in path.read_text().splitlines() if text.startswith("Licence:")), None)
    assert line is not None, f"{path.relative_to(REPO)} records no licence ('Licence: …')"
    return line


def test_the_roster_art_and_the_world_art_record_their_licence() -> None:
    """Each record says the art ships under the repository's MIT licence. The world art's, and the notices' row for
    it, say by which of its generator's terms the generating account owns it: PixelLab's §1.3 and §3.3."""
    for path in (APPEARANCE_SOURCES, WORLD_SOURCES):
        line = _licence(path)
        assert "MIT" in line and "LICENSE" in line, line
    world = _licence(WORLD_SOURCES)
    assert "PixelLab" in world and "§1.3" in world and "§3.3" in world, world
    row = next((r for r in _images_section().splitlines() if "`frontend/public/world/`" in r), None)
    assert row is not None, "the notices have no row for the world art"
    assert "PixelLab" in row and "(§1.3, §3.3)" in row, f"the row does not cite PixelLab's ownership terms: {row}"


def test_every_image_the_repository_keeps_is_under_a_place_the_notices_name() -> None:
    """Every image file the repository tracks, found by its extension, and every image drawn in code."""
    places = re.findall(r"`([^`]+)`", _images_section())
    images = [name for name in _files() if IMAGE.search(name)]
    assert images, "no image found: the listing is broken"
    unnamed = sorted(
        name
        for name in [*images, *DRAWN_IN_CODE]
        if not any(name == p or (p.endswith("/") and name.startswith(p)) for p in places)
    )
    assert not unnamed, f"images in no place THIRD_PARTY_NOTICES.md's Images section names: {unnamed}"
