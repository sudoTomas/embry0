"""The world's art manifest (frontend/public/world/manifest.json) names images that exist, inside their sheets."""

from __future__ import annotations

import json
import struct
from pathlib import Path

from embry0.services.lead_kinds import GLYPH_CREATURES, KINDS

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


#: What the manifest's ``interaction`` may say (frontend art/manifest.ts types it so).
INTERACTIONS = {"attack", "talk", "start_quest", "repair"}


def _interaction(verb: str) -> str:
    """The manifest's word for a verb: ``Start quest`` is ``start_quest``."""
    return verb.lower().replace(" ", "_")


def test_the_manifest_says_what_the_lead_kind_catalogue_says() -> None:
    """The manifest names the creature each lead kind stands as on its own, because it is an art
    file the frontend reads; the catalogue (embry0.services.lead_kinds) is the source of truth.
    Every kind the art draws must be listed once, under the catalogue's creature, with the
    catalogue's verb, and the art must list no kind the catalogue does not know."""
    problems: list[str] = []
    listed: dict[str, str] = {}
    for name, creature in MANIFEST["creatures"].items():
        if creature["interaction"] not in INTERACTIONS:
            problems.append(f"{name}'s interaction {creature['interaction']!r} is not one of {sorted(INTERACTIONS)}")
        for kind in creature["leadKinds"]:
            if kind in listed:
                problems.append(f"{kind} is listed under both {listed[kind]} and {name}")
            listed[kind] = name
            known = KINDS.get(kind)
            if known is None:
                problems.append(f"{name} lists {kind!r}, which the lead-kind catalogue does not know")
                continue
            if known.creature != name:
                problems.append(f"{kind} stands as {known.creature} in the catalogue but under {name} in the manifest")
            if creature["interaction"] != _interaction(known.verb):
                problems.append(
                    f"{name}'s interaction is {creature['interaction']!r}, "
                    f"but {kind}'s verb {known.verb!r} says {_interaction(known.verb)!r}"
                )
    for kind in KINDS.values():
        if kind.creature not in GLYPH_CREATURES and kind.id not in listed:
            problems.append(
                f"{kind.id} is drawn by the art ({kind.creature}), but no creature in the manifest lists it"
            )
    assert not problems, "the art manifest and the lead-kind catalogue disagree:\n" + "\n".join(problems)


def test_a_creature_with_no_sheet_has_its_glyph_and_no_art_claims_its_kind() -> None:
    """The Slop Dungeon's ooze (spec 2026-10-25) is an original 12x12 glyph until the art draws it."""
    glyphs = (REPO / "frontend" / "src" / "components" / "world" / "CreatureIcon.tsx").read_text()
    listed = {kind for creature in MANIFEST["creatures"].values() for kind in creature["leadKinds"]}
    for kind, drawn in KINDS.items():
        if drawn.creature in GLYPH_CREATURES:
            assert f"  {drawn.creature}: {{" in glyphs, f"{drawn.creature} has no glyph in CreatureIcon"
            assert kind not in listed, f"{kind} is drawn by the art; drop {drawn.creature} from GLYPH_CREATURES"


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
