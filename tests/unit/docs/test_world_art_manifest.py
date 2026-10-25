"""The world's art manifest (frontend/public/world/manifest.json) names images that exist, inside their sheets."""

from __future__ import annotations

import json
import struct
from pathlib import Path

from embry0.services.world import LEAD_KINDS

REPO = Path(__file__).resolve().parents[3]
WORLD = REPO / "frontend" / "public" / "world"
MANIFEST = json.loads((WORLD / "manifest.json").read_text())


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _inside(rect: list[int], size: tuple[int, int]) -> bool:
    x, y, w, h = rect
    return x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= size[0] and y + h <= size[1]


def test_every_atlas_is_a_power_of_two_png_of_the_size_it_says() -> None:
    for name, atlas in MANIFEST["atlases"].items():
        width, height = _png_size(WORLD / atlas["image"])
        assert (width, height) == (atlas["width"], atlas["height"]), name
        assert width & (width - 1) == 0 and height & (height - 1) == 0, f"{name} is {width}x{height}"


def test_every_tileset_is_a_4x4_corner_sheet_of_the_tile_size() -> None:
    tile = MANIFEST["tileSize"]
    for name, tileset in MANIFEST["tilesets"].items():
        assert tileset["layout"] == "corner-4x4", name
        assert _png_size(WORLD / tileset["image"]) == (4 * tile, 4 * tile), name
        assert set(tileset["terrains"]) == {"0", "1"}, name


def test_every_frame_lies_inside_its_sheet_and_every_anchor_inside_its_frame() -> None:
    sizes = {name: (a["width"], a["height"]) for name, a in MANIFEST["atlases"].items()}
    for name, obj in MANIFEST["objects"].items():
        assert _inside(obj["rect"], sizes[obj["atlas"]]), name
        assert 0 <= obj["anchor"]["x"] < obj["rect"][2] and 0 <= obj["anchor"]["y"] < obj["rect"][3], name
    for name, creature in MANIFEST["creatures"].items():
        assert len(creature["frames"]) >= 2 and creature["fps"] > 0, name
        for rect in creature["frames"]:
            assert _inside(rect, sizes[creature["atlas"]]), name
    for name, effect in MANIFEST["vfx"].items():
        size = _png_size(WORLD / effect["image"])
        for rect in effect["frames"]:
            assert _inside(rect, size), name


def test_every_lead_kind_has_a_creature_and_one_only() -> None:
    kinds = [kind for creature in MANIFEST["creatures"].values() for kind in creature["leadKinds"]]
    assert set(kinds) >= set(LEAD_KINDS) and len(kinds) == len(set(kinds))
    assert {c["interaction"] for c in MANIFEST["creatures"].values()} <= {"attack", "talk", "start_quest", "repair"}


def test_the_backend_draws_each_kind_as_the_creature_the_art_lists_it_under() -> None:
    for kind, drawn in LEAD_KINDS.items():
        assert kind in MANIFEST["creatures"][drawn.creature]["leadKinds"], kind


def test_every_asset_has_a_recorded_source() -> None:
    sources = (WORLD / "SOURCES.md").read_text()
    for name in [*MANIFEST["objects"], *MANIFEST["creatures"], *MANIFEST["tilesets"], *MANIFEST["vfx"]]:
        assert (
            f"| {name}" in sources
            or f"| tileset_{name}" in sources
            or f"| obj_{name}" in sources
            or f"| creature_{name}" in sources
            or f"| vfx_{name}" in sources
        ), name
