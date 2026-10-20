#!/usr/bin/env python3
"""The roster's pixel art: twelve 16x16 party sprites from one chibi template plus
per-class headgear, props and poses. Rewrites the avatar blocks of
frontend/src/components/roster/sprites.ts (idle, tool, done, failed per
avatar; figure() there derives the other states):

    python3 scripts/roster_sprites.py frontend/src/components/roster/sprites.ts

Edit the templates, headgear or props here, run it, then re-render the
contact sheet next to the game UI plan.

Classes: o outline (dark), f fill (palette colour), h highlight (light face,
props), e eyes (dark). Row 0 of idle stays empty for the bob and the spark.
"""

import sys

W = H = 16


class G:
    def __init__(self, rows=None):
        self.g = [list(r) for r in rows] if rows else [["."] * W for _ in range(H)]

    def put(self, x, y, c):
        if 0 <= x < W and 0 <= y < H:
            self.g[y][x] = c

    def h(self, x0, x1, y, c):
        for x in range(x0, x1 + 1):
            self.put(x, y, c)

    def v(self, x, y0, y1, c):
        for y in range(y0, y1 + 1):
            self.put(x, y, c)

    def rect(self, x0, y0, x1, y1, c):
        for y in range(y0, y1 + 1):
            self.h(x0, x1, y, c)

    def box(self, x0, y0, x1, y1, fill, line="o"):
        self.rect(x0, y0, x1, y1, fill)
        self.h(x0, x1, y0, line)
        self.h(x0, x1, y1, line)
        self.v(x0, y0, y1, line)
        self.v(x1, y0, y1, line)

    def rows(self):
        return ["".join(r) for r in self.g]


# --- the body templates (head rows 2..7, torso 8..12, legs 13..15) ------------
# Narrow: torso cols 5..10, arms at 3 and 12. Wide: torso 4..11, arms at 2 and 13.

NARROW = {
    "idle": [
        "................",
        "................",
        "....oooooooo....",
        "....ohhhhhho....",
        "....ohehheho....",
        "....ohhhhhho....",
        "....ohhhhhho....",
        "....oooooooo....",
        "..oooffffffooo..",
        "..ofoffffffofo..",
        "..ofoffffffofo..",
        "..ofoffffffofo..",
        "..oooffffffooo..",
        "....offooffo....",
        "....offooffo....",
        "....oooooooo....",
    ],
    "tool": [
        "................",
        "................",
        "....oooooooo.o..",
        "....ohhhhhhoofo.",
        "....ohehhehoofo.",
        "....ohhhhhhoofo.",
        "....ohhhhhhoofo.",
        "....ooooooooofo.",
        "..oooffffffofo..",
        "..ofoffffffoo...",
        "..ofoffffffo....",
        "..ofoffffffo....",
        "..oooffffffo....",
        "....offooffo....",
        "....offooffo....",
        "....oooooooo....",
    ],
    "done": [
        "................",
        "................",
        "..o.oooooooo.o..",
        ".ofoohhhhhhoofo.",
        ".ofoohehhehoofo.",
        ".ofoohhhhhhoofo.",
        ".ofoohhhhhhoofo.",
        ".ofoooooooooofo.",
        "..ofoffffffofo..",
        "...ooffffffoo...",
        "....offffffo....",
        "....offffffo....",
        "....offffffo....",
        "....offooffo....",
        "....offooffo....",
        "....oooooooo....",
    ],
    "failed": [
        "................",
        "................",
        "....oooooooo....",
        "....ohhhhhho.h..",
        "....ohhhhhho....",
        "....ohehheho....",
        "....ohhoohho....",
        "....oooooooo....",
        "...ooffffffoo...",
        "..ofoffffffofo..",
        "..ofoffffffofo..",
        "..ofoffffffofo..",
        "..ofoffffffofo..",
        "..oooffooffooo..",
        "....offooffo....",
        "....oooooooo....",
    ],
}

WIDE = {
    "idle": [
        "................",
        "................",
        "....oooooooo....",
        "....ohhhhhho....",
        "....ohehheho....",
        "....ohhhhhho....",
        "....ohhhhhho....",
        "...oooooooooo...",
        ".ooooffffffffooo",
        ".ofoffffffffofo.",
        ".ofoffffffffofo.",
        ".ofoffffffffofo.",
        ".oooffffffffooo.",
        "...offfooofffo..",
        "...offfooofffo..",
        "...oooooooooo...",
    ],
    "tool": [
        "................",
        "................",
        "....oooooooo..o.",
        "....ohhhhhho.ofo",
        "....ohehheho.ofo",
        "....ohhhhhho.ofo",
        "....ohhhhhho.ofo",
        "...oooooooooo.fo",
        ".ooooffffffffofo",
        ".ofoffffffffoo..",
        ".ofoffffffffo...",
        ".ofoffffffffo...",
        ".oooffffffffo...",
        "...offfooofffo..",
        "...offfooofffo..",
        "...oooooooooo...",
    ],
    "done": [
        "................",
        "................",
        ".o..oooooooo..o.",
        "ofo.ohhhhhho.ofo",
        "ofo.ohehheho.ofo",
        "ofo.ohhhhhho.ofo",
        "ofo.ohhhhhho.ofo",
        "ofooooooooooo.fo",
        ".ofoffffffffofo.",
        "..ooffffffffoo..",
        "...offffffffo...",
        "...offffffffo...",
        "...offffffffo...",
        "...offfooofffo..",
        "...offfooofffo..",
        "...oooooooooo...",
    ],
    "failed": [
        "................",
        "................",
        "....oooooooo....",
        "....ohhhhhho.h..",
        "....ohhhhhho....",
        "....ohehheho....",
        "....ohhoohho....",
        "...oooooooooo...",
        "..ooffffffffoo..",
        ".ofoffffffffofo.",
        ".ofoffffffffofo.",
        ".ofoffffffffofo.",
        ".ofoffffffffofo.",
        ".ooooffooofffoo.",
        "...offfooofffo..",
        "...oooooooooo...",
    ],
}


# --- headgear (rows 0..7; drawn over the template) -----------------------------


def hood(g):
    # pointed hood: peak at row 1, brim shading the brow, sides to the shoulders
    g.h(7, 8, 1, "o")
    g.h(5, 10, 2, "o")
    g.h(6, 9, 2, "f")
    g.h(7, 8, 1, "f")
    g.put(4, 2, "o")
    g.put(11, 2, "o")
    g.put(5, 2, "o")
    g.put(10, 2, "o")
    g.h(5, 10, 3, "f")
    g.v(3, 3, 7, "o")
    g.v(12, 3, 7, "o")
    g.v(4, 3, 7, "f")
    g.v(11, 3, 7, "f")
    g.h(4, 11, 7, "o")


def helmet(g):
    g.h(6, 9, 1, "o")
    g.h(7, 8, 1, "h")
    g.h(4, 11, 2, "o")
    g.h(5, 10, 2, "f")
    g.h(5, 10, 3, "f")
    g.h(5, 10, 6, "f")
    g.put(4, 6, "o")
    g.put(11, 6, "o")  # chin guard
    g.put(5, 4, "o")
    g.put(10, 4, "o")  # visor edges beside the eyes


def cap(g):
    g.h(5, 10, 1, "o")
    g.h(6, 9, 1, "f")
    g.h(4, 11, 2, "o")
    g.h(5, 10, 2, "f")
    g.h(3, 12, 3, "o")


def winged_cap(g):
    cap(g)
    g.put(2, 2, "h")
    g.put(3, 2, "h")
    g.put(12, 2, "h")
    g.put(13, 2, "h")
    g.put(1, 1, "h")
    g.put(14, 1, "h")


def hard_hat(g):
    g.h(6, 9, 1, "o")
    g.h(7, 8, 1, "h")
    g.h(4, 11, 2, "o")
    g.h(5, 10, 2, "h")
    g.h(3, 12, 3, "o")
    g.h(4, 11, 3, "h")
    g.put(3, 3, "o")
    g.put(12, 3, "o")


def mortarboard(g):
    g.h(3, 12, 1, "o")
    g.h(4, 11, 1, "f")
    g.h(3, 12, 2, "o")
    g.h(4, 11, 2, "f")
    g.put(12, 2, "h")
    g.put(13, 2, "o")
    g.put(13, 3, "h")
    # glasses: a bar across the eyes with lenses around them
    g.h(5, 10, 4, "o")
    g.put(6, 4, "e")
    g.put(9, 4, "e")


def feathered_hat(g):
    g.h(5, 10, 1, "o")
    g.h(6, 9, 1, "f")
    g.h(3, 12, 2, "o")
    g.h(4, 11, 2, "f")
    g.put(12, 1, "h")
    g.put(13, 1, "h")
    g.put(14, 1, "h")
    g.put(13, 2, "o")


def goggles(g):
    g.h(4, 11, 3, "o")
    g.h(5, 10, 3, "f")  # strap
    for x in (5, 7, 8, 10):
        g.put(x, 4, "o")
    g.h(4, 11, 2, "o")
    g.h(5, 10, 2, "f")  # cap under the strap


def veil(g):
    g.h(5, 10, 1, "o")
    g.h(6, 9, 1, "h")
    g.put(4, 2, "h")
    g.put(11, 2, "h")
    g.v(3, 3, 7, "h")
    g.v(12, 3, 7, "h")
    g.v(2, 3, 7, "o")
    g.v(13, 3, 7, "o")
    g.put(7, 2, "h")
    g.put(8, 2, "h")


def wig(g):
    # the arbiter's judge wig: rolled sides and a flat top
    g.h(4, 11, 1, "o")
    g.h(5, 10, 1, "h")
    g.h(4, 11, 2, "h")
    for x in (3, 12):
        g.v(x, 2, 6, "h")
        g.put(x, 7, "o")
    g.v(2, 3, 6, "o")
    g.v(13, 3, 6, "o")
    g.put(2, 2, "o")
    g.put(13, 2, "o")


def headband(g):
    g.h(5, 10, 2, "f")
    g.h(4, 11, 1, "o")
    g.h(5, 10, 1, "f")


def apron(g):
    g.rect(6, 9, 9, 12, "h")
    g.v(6, 9, 12, "o")
    g.v(9, 9, 12, "o")


def mask(g):
    g.h(5, 10, 5, "o")
    g.h(5, 10, 6, "f")


def stone(g):
    # the golem: a square head, a seam and cracks
    g.h(4, 11, 1, "o")
    g.h(5, 10, 1, "f")
    g.h(5, 10, 2, "f")
    g.put(5, 3, "o")
    g.put(10, 6, "o")
    g.put(7, 10, "o")
    g.put(8, 11, "o")
    g.h(5, 10, 3, "f")
    g.h(5, 10, 5, "f")
    g.h(5, 10, 6, "f")


# --- props at the raised right hand (cols 11..15, rows 0..7) -------------------


def prop_orb(g):
    g.box(11, 0, 14, 3, "h")
    g.put(13, 2, "f")
    g.put(12, 1, "h")


def prop_hammer(g):
    g.h(11, 15, 0, "o")
    g.h(11, 15, 1, "o")
    g.h(12, 14, 1, "f")
    g.h(11, 15, 2, "o")
    g.put(13, 3, "o")
    g.put(13, 4, "o")


def prop_shield(g):
    g.rect(12, 1, 15, 6, "f")
    g.v(15, 1, 6, "o")
    g.h(12, 15, 1, "o")
    g.h(12, 15, 6, "o")
    g.put(13, 3, "h")
    g.put(13, 4, "h")


def prop_flask(g):
    g.h(13, 14, 0, "o")
    g.v(13, 1, 2, "o")
    g.v(14, 1, 2, "o")
    g.box(12, 3, 15, 6, "h")
    g.h(13, 14, 5, "f")


def prop_horn(g):
    g.h(11, 15, 2, "o")
    g.h(12, 14, 3, "h")
    g.put(11, 3, "o")
    g.put(15, 3, "o")
    g.h(11, 15, 4, "o")
    g.put(15, 1, "o")
    g.put(15, 5, "o")


def prop_scales(g):
    g.v(13, 0, 2, "o")
    g.h(10, 15, 1, "o")
    g.put(10, 2, "o")
    g.put(15, 2, "o")
    g.h(9, 11, 3, "h")
    g.h(14, 15, 3, "h")
    g.put(13, 0, "h")


def prop_book(g):
    g.box(11, 1, 15, 5, "h")
    g.v(13, 1, 5, "o")
    g.put(12, 3, "f")
    g.put(14, 3, "f")


def prop_wrench(g):
    g.v(13, 1, 6, "o")
    g.h(12, 14, 0, "o")
    g.put(12, 1, "o")
    g.put(14, 1, "o")
    g.put(13, 0, "h")
    g.put(13, 1, "h")


def prop_dagger(g):
    g.v(13, 0, 4, "h")
    g.put(13, 0, "o")
    g.h(12, 14, 5, "o")
    g.put(13, 6, "o")


def prop_lute(g):
    g.box(11, 2, 15, 6, "f")
    g.put(13, 4, "o")
    g.v(13, 0, 1, "o")
    g.put(12, 0, "o")
    g.put(14, 0, "o")
    g.put(12, 3, "h")


def prop_rock(g):
    g.box(11, 0, 15, 4, "f")
    g.put(12, 1, "h")
    g.put(14, 3, "o")
    g.put(13, 2, "h")


# --- avatars -----------------------------------------------------------------
# name, spark column, body, headgear, torso decoration, prop

AVATARS = [
    ("seer", 10, NARROW, hood, None, prop_orb),
    ("smith", 11, WIDE, headband, apron, prop_hammer),
    ("warden", 11, WIDE, helmet, None, prop_shield),
    ("alchemist", 11, NARROW, goggles, None, prop_flask),
    ("herald", 12, NARROW, winged_cap, None, prop_horn),
    ("arbiter", 12, NARROW, wig, None, prop_scales),
    ("scholar", 12, NARROW, mortarboard, None, prop_book),
    ("oracle", 11, NARROW, veil, None, prop_orb),
    ("engineer", 11, WIDE, hard_hat, None, prop_wrench),
    ("rogue", 10, NARROW, lambda g: (hood(g), mask(g)), None, prop_dagger),
    ("bard", 10, NARROW, feathered_hat, None, prop_lute),
    ("golem", 11, WIDE, stone, None, prop_rock),
]


def draw(body, headgear, deco, prop):
    out = {}
    for state in ("idle", "tool", "done", "failed"):
        g = G(body[state])
        if headgear:
            headgear(g)
        if deco:
            deco(g)
        if state == "tool":
            prop(g)
        if state == "failed":
            # the head sits one row lower on the failed template: eyes and
            # frown are drawn there already; keep the sweat drop
            pass
        out[state] = g.rows()
    return out


def emit():
    out = []
    for name, spark, body, headgear, deco, prop in AVATARS:
        frames = draw(body, headgear, deco, prop)
        out.append(f"const {name} = figure({spark}, {{")
        for state in ("idle", "tool", "done", "failed"):
            out.append(f"  {state}: [")
            for row in frames[state]:
                assert len(row) == 16 and set(row) <= set(".ofhe"), (name, state, row)
                out.append(f'    "{row}",')
            out.append("  ],")
        out.append("});\n")
    return "\n".join(out)


def splice(path: str) -> None:
    """Replace the avatar blocks of sprites.ts (from `const seer` to
    `export const SPRITES`) with freshly emitted ones."""
    source = open(path).read()
    start = source.index("const seer = figure(")
    end = source.index("export const SPRITES")
    open(path, "w").write(source[:start] + emit() + "\n" + source[end:])


if __name__ == "__main__":
    if len(sys.argv) > 1:
        splice(sys.argv[1])
        print("spliced", sys.argv[1])
    else:
        print(emit())
