#!/usr/bin/env python3
"""A second roster in the spirit of an old-school MMO avatar: taller human
proportions (head 5 rows, tunic, belt, legs, boots), gear always in hand,
capes, full helms, beards and one partyhat. Same frame format and states as
sprites.ts; same Drawn contract (idle, tool, done, failed).

Classes: o outline (dark), f fill (palette), h highlight (skin, metal, cloth
trim), e eyes (dark). Row 0 of idle stays empty for the bob and the spark.
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


# --- the body: head rows 1..5 (face h, eyes e at (6,3) (9,3)), tunic 6..10 with a
# belt at 9, legs 11..13, boots 14, ground 15 empty. Arms at cols 3 and 12.
# The right hand (col 12) holds the gear: idle lowered beside the leg, tool raised.

HUMAN = {
    "idle": [
        "................",
        ".....oooooo.....",
        "....ohhhhhho....",
        "....ohehheho....",
        "....ohhhhhho....",
        ".....oooooo.....",
        "...ooffffffoo...",
        "..ofoffffffofo..",
        "..ofoffffffofo..",
        "..ohoooooooogo..",
        "..ofoffffffofo..",
        "...ooffffffoo...",
        "....offooffo....",
        "....offooffo....",
        "....ohhooHho....",
        "....ooo..ooo....",
    ],
    "tool": [
        "................",
        ".....oooooo..o..",
        "....ohhhhhhoofo.",
        "....ohehhehoofo.",
        "....ohhhhhhoofo.",
        ".....ooooooofo..",
        "...oofffffffo...",
        "..ofoffffffo....",
        "..ofoffffffo....",
        "..ohoooooooo....",
        "..ofoffffffo....",
        "...ooffffffo....",
        "....offooffo....",
        "....offooffo....",
        "....ohhooHho....",
        "....ooo..ooo....",
    ],
    "done": [
        "................",
        "..o..oooooo..o..",
        ".ofoohhhhhhoofo.",
        ".ofoohehhehoofo.",
        ".ofoohhhhhhoofo.",
        ".ofo.oooooo.ofo.",
        ".ofooffffffoofo.",
        "..ooffffffffoo..",
        "....offffffo....",
        "....ooooooooo...",
        "....offffffo....",
        "....offffffo....",
        "....offooffo....",
        "....offooffo....",
        "....ohhooHho....",
        "....ooo..ooo....",
    ],
    "failed": [
        "................",
        "................",
        ".....oooooo..h..",
        "....ohhhhhho....",
        "....ohehheho....",
        "....ohhoohho....",
        "...oooooooooo...",
        "..ofoffffffofo..",
        "..ofoffffffofo..",
        "..ohoooooooogo..",
        "..ofoffffffofo..",
        "..ofoffffffofo..",
        "...ooffooffoo...",
        "....offooffo....",
        "....ohhooHho....",
        "....ooo..ooo....",
    ],
}

# A broad, short dwarf: head rows 2..6, a big beard, wide tunic, short legs.
DWARF = {
    "idle": [
        "................",
        "................",
        ".....oooooo.....",
        "....ohhhhhho....",
        "....ohehheho....",
        "....ohhhhhho....",
        "...oohhhhhhoo...",
        "..ofohhhhhhofo..",
        "..ofoffffffofo..",
        "..ohoooooooogo..",
        "..ofoffffffofo..",
        "...ooffffffoo...",
        "....offooffo....",
        "....offooffo....",
        "....ohhooHho....",
        "....ooo..ooo....",
    ],
    "tool": [
        "................",
        "............o...",
        ".....oooooooofo.",
        "....ohhhhhhoofo.",
        "....ohehhehoofo.",
        "....ohhhhhhoofo.",
        "...oohhhhhhofo..",
        "..ofohhhhhhoo...",
        "..ofoffffffo....",
        "..ohoooooooo....",
        "..ofoffffffo....",
        "...ooffffffo....",
        "....offooffo....",
        "....offooffo....",
        "....ohhooHho....",
        "....ooo..ooo....",
    ],
    "done": [
        "................",
        "..o..........o..",
        ".ofo.oooooo.ofo.",
        ".ofoohhhhhhoofo.",
        ".ofoohehhehoofo.",
        ".ofoohhhhhhoofo.",
        ".ofoohhhhhhoofo.",
        "..oooohhhhhoooo.",
        "....offffffo....",
        "....ooooooooo...",
        "....offffffo....",
        "....offffffo....",
        "....offooffo....",
        "....offooffo....",
        "....ohhooHho....",
        "....ooo..ooo....",
    ],
    "failed": [
        "................",
        "................",
        "................",
        ".....oooooo..h..",
        "....ohhhhhho....",
        "....ohehheho....",
        "....ohhoohho....",
        "...oohhhhhhoo...",
        "..ofohhhhhhofo..",
        "..ohoooooooogo..",
        "..ofoffffffofo..",
        "..ofoffffffofo..",
        "...ooffooffoo...",
        "....offooffo....",
        "....ohhooHho....",
        "....ooo..ooo....",
    ],
}


def normalise(rows):
    # 'g' marks the gear hand (right), 'H' a boot highlight placeholder: both
    # become plain cells after the gear and boots are drawn.
    return [r.replace("g", "f").replace("H", "h") for r in rows]


# --- headgear and body gear -----------------------------------------------------


def wizard_hat(g):
    # tall point, wide brim shading the brow
    g.h(7, 8, 0, ".")
    g.put(7, 1, "o")
    g.put(8, 1, "o")
    g.h(6, 9, 1, "o")
    g.h(7, 8, 1, "f")
    g.h(5, 10, 2, "o")
    g.h(6, 9, 2, "f")
    g.h(3, 12, 3, "o")
    g.h(4, 11, 3, "f")
    g.put(3, 3, "o")
    g.put(12, 3, "o")
    # the brim sits at row 3: face moves to rows 4..5 with eyes at row 4
    g.h(5, 10, 4, "h")
    g.put(4, 4, "o")
    g.put(11, 4, "o")
    g.put(6, 4, "e")
    g.put(9, 4, "e")
    g.h(5, 10, 5, "h")
    g.put(4, 5, "o")
    g.put(11, 5, "o")
    g.h(4, 11, 6, "o")


def full_helm(g):
    # a knight's helm: metal all over, a T slit for the eyes
    g.h(5, 10, 1, "o")
    g.h(4, 11, 2, "o")
    g.h(5, 10, 2, "h")
    g.h(5, 10, 3, "h")
    g.h(5, 10, 4, "h")
    g.put(6, 3, "e")
    g.put(9, 3, "e")
    g.h(7, 8, 3, "o")
    g.h(7, 8, 4, "o")
    g.put(7, 1, "f")
    g.put(8, 1, "f")  # plume


def hood(g, beard=False):
    g.h(6, 9, 1, "o")
    g.h(5, 10, 1, "f")
    g.put(4, 1, "o")
    g.put(11, 1, "o")
    g.h(5, 10, 2, "f")
    g.put(5, 3, "f")
    g.put(10, 3, "f")
    g.put(5, 4, "f")
    g.put(10, 4, "f")
    g.v(3, 2, 5, "o")
    g.v(12, 2, 5, "o")
    g.v(4, 2, 5, "f")
    g.v(11, 2, 5, "f")
    if beard:
        g.h(6, 9, 5, "h")
        g.h(5, 10, 6, "h")
        g.put(4, 6, "o")
        g.put(11, 6, "o")


def beard(g, top=5):
    g.h(6, 9, top, "h")
    g.h(5, 10, top + 1, "h")
    g.h(6, 9, top + 2, "h")
    g.h(7, 8, top + 3, "h")
    g.put(5, top + 2, "o")
    g.put(10, top + 2, "o")
    g.put(6, top + 3, "o")
    g.put(9, top + 3, "o")


def partyhat(g):
    # the zigzag paper crown: three points over a band
    g.h(4, 11, 1, ".")
    for x in (5, 7, 9):
        g.put(x, 1, "h")
        g.put(x + 1, 1, ".")
    g.put(10, 1, "h")
    g.h(5, 10, 2, "h")
    g.put(4, 2, "o")
    g.put(11, 2, "o")


def mining_helmet(g):
    g.h(5, 10, 1, "o")
    g.h(6, 9, 1, "f")
    g.h(4, 11, 2, "o")
    g.h(5, 10, 2, "f")
    g.put(7, 2, "h")
    g.put(8, 2, "h")


def hair(g):
    g.h(5, 10, 1, "o")
    g.h(5, 10, 2, "f")
    g.put(4, 2, "o")
    g.put(11, 2, "o")
    g.put(7, 2, "h")


def goggles(g):
    hair(g)
    for x in (5, 7, 8, 10):
        g.put(x, 3, "o")


def feathered_cap(g):
    g.h(5, 10, 1, "o")
    g.h(6, 9, 1, "f")
    g.h(4, 11, 2, "o")
    g.h(5, 10, 2, "f")
    g.put(11, 1, "h")
    g.put(12, 1, "h")
    g.put(13, 0 + 1, "h")
    g.put(13, 2, "o")


def wig(g):
    g.h(5, 10, 1, "o")
    g.h(5, 10, 2, "h")
    for x in (4, 11):
        g.v(x, 2, 5, "h")
    g.v(3, 2, 5, "o")
    g.v(12, 2, 5, "o")
    g.put(3, 1, "o")
    g.put(12, 1, "o")


def bandana(g):
    g.h(5, 10, 1, "o")
    g.h(5, 10, 2, "f")
    g.put(4, 2, "o")
    g.put(11, 2, "f")
    g.put(12, 2, "o")
    g.put(12, 3, "f")
    g.put(13, 3, "o")


def mortarboard(g):
    g.h(3, 12, 1, "o")
    g.h(4, 11, 1, "f")
    g.put(12, 2, "h")
    g.put(13, 2, "o")
    g.h(5, 10, 3, "o")
    g.put(6, 3, "e")
    g.put(9, 3, "e")  # glasses


def stone_head(g):
    # a block of stone with a seam; the eyes stay on the template's row 4
    g.h(5, 10, 1, "o")
    g.h(5, 10, 2, "f")
    g.put(6, 2, "o")
    g.put(9, 5, "o")
    g.put(5, 3, "o")


def cape(g, side="left"):
    # a cape hangs behind the arm on one side
    x = 2 if side == "left" else 13
    g.v(x, 6, 11, "h")
    g.v(x - 1 if side == "left" else x + 1, 6, 11, "o")
    g.put(x, 12, "o")


def platebody(g):
    g.rect(5, 6, 10, 8, "h")
    g.put(7, 7, "o")
    g.put(8, 7, "o")
    g.h(3, 4, 6, "h")
    g.h(11, 12, 6, "h")
    g.put(2, 6, "o")
    g.put(13, 6, "o")  # pauldrons


def apron(g):
    g.rect(6, 7, 9, 10, "h")
    g.put(6, 9, "h")
    g.put(9, 9, "h")


def belt_buckle(g):
    g.put(7, 9, "h")
    g.put(8, 9, "h")


def robe(g):
    # robe instead of legs: a skirt down to the boots
    g.rect(5, 11, 10, 13, "f")
    g.v(4, 11, 13, "o")
    g.v(11, 11, 13, "o")
    g.put(7, 12, "o")


# --- gear, drawn at the right hand: lowered (idle, failed) or raised (tool) -----
# Each is a pair (lowered, raised) of draw functions.


def staff_low(g):
    g.v(13, 5, 14, "o")
    g.h(12, 14, 3, "o")
    g.h(12, 14, 4, "h")
    g.put(13, 4, "f")
    g.h(12, 14, 2, "o")


def staff_up(g):
    g.v(13, 3, 8, "o")
    g.box(11, 0, 15, 2, "h")
    g.put(13, 1, "f")


def pick_low(g):
    g.v(13, 6, 14, "o")
    g.h(11, 15, 5, "h")
    g.h(11, 15, 4, "o")
    g.put(11, 6, "o")
    g.put(15, 6, "o")


def pick_up(g):
    g.v(13, 2, 8, "o")
    g.h(11, 15, 1, "h")
    g.h(11, 15, 0, "o")
    g.put(11, 2, "o")
    g.put(15, 2, "o")


def sword_low(g):
    g.v(13, 7, 13, "h")
    g.put(13, 14, "o")
    g.h(11, 15, 6, "o")
    g.put(13, 5, "f")
    g.put(13, 4, "o")
    # and a shield on the left arm
    g.box(0, 6, 3, 11, "f")
    g.put(1, 8, "h")
    g.put(2, 8, "h")


def sword_up(g):
    g.v(13, 1, 6, "h")
    g.put(13, 0, "o")
    g.h(11, 15, 7, "o")
    g.put(13, 8, "f")
    g.put(13, 9, "o")
    g.box(0, 6, 3, 11, "f")
    g.put(1, 8, "h")
    g.put(2, 8, "h")


def flask_low(g):
    g.h(13, 14, 8, "o")
    g.box(12, 9, 15, 12, "h")
    g.h(13, 14, 11, "f")
    g.put(13, 7, "o")
    g.put(14, 7, "o")


def flask_up(g):
    g.h(13, 14, 0, "o")
    g.v(13, 1, 2, "o")
    g.v(14, 1, 2, "o")
    g.box(12, 3, 15, 6, "h")
    g.h(13, 14, 5, "f")


def bell_low(g):
    g.put(13, 8, "o")
    g.box(12, 9, 15, 12, "h")
    g.h(13, 14, 11, "f")
    g.put(13, 13, "o")


def bell_up(g):
    g.put(13, 0, "o")
    g.box(12, 1, 15, 4, "h")
    g.h(13, 14, 3, "f")
    g.put(13, 5, "o")
    g.put(14, 5, "o")


def scales_low(g):
    g.v(13, 7, 14, "o")
    g.h(11, 15, 8, "o")
    g.put(11, 9, "h")
    g.put(10, 9, "h")
    g.put(15, 9, "h")
    g.put(14, 9, "h")


def scales_up(g):
    g.v(13, 1, 8, "o")
    g.h(10, 15, 1, "o")
    g.put(10, 2, "o")
    g.put(15, 2, "o")
    g.h(9, 11, 3, "h")
    g.h(14, 15, 3, "h")


def book_low(g):
    g.box(12, 8, 15, 12, "h")
    g.v(13, 8, 12, "o")


def book_up(g):
    g.box(11, 1, 15, 5, "h")
    g.v(13, 1, 5, "o")
    g.put(12, 3, "f")
    g.put(14, 3, "f")


def orb_low(g):
    g.box(12, 9, 15, 12, "h")
    g.put(13, 10, "f")


def orb_up(g):
    g.box(11, 0, 14, 3, "h")
    g.put(12, 1, "f")
    g.put(13, 2, "f")


def hammer_low(g):
    g.v(13, 7, 14, "o")
    g.box(11, 4, 15, 6, "h")


def hammer_up(g):
    g.v(13, 3, 8, "o")
    g.box(11, 0, 15, 2, "h")


def dagger_low(g):
    g.v(13, 9, 12, "h")
    g.put(13, 13, "o")
    g.h(12, 14, 8, "o")
    g.put(13, 7, "f")
    g.put(13, 6, "o")


def dagger_up(g):
    g.v(13, 1, 5, "h")
    g.put(13, 1, "o")
    g.h(12, 14, 6, "o")
    g.put(13, 7, "o")


def lute_low(g):
    g.box(12, 9, 15, 13, "f")
    g.put(13, 11, "o")
    g.v(13, 7, 8, "o")
    g.put(12, 7, "o")
    g.put(14, 7, "o")


def lute_up(g):
    g.box(11, 2, 15, 6, "f")
    g.put(13, 4, "o")
    g.v(13, 0, 1, "o")
    g.put(12, 0, "o")
    g.put(14, 0, "o")
    g.put(12, 3, "h")


def rock_low(g):
    g.box(12, 9, 15, 13, "f")
    g.put(13, 10, "h")
    g.put(14, 12, "o")


def rock_up(g):
    g.box(11, 0, 15, 4, "f")
    g.put(12, 1, "h")
    g.put(13, 2, "h")
    g.put(14, 3, "o")


# --- avatars --------------------------------------------------------------------
# name, spark column, body, head, extras (drawn on every frame), gear (low, up)

AVATARS = [
    ("seer", 10, HUMAN, wizard_hat, [lambda g: cape(g, "left"), robe], (staff_low, staff_up)),
    ("smith", 11, HUMAN, mining_helmet, [apron, belt_buckle], (hammer_low, hammer_up)),
    ("warden", 11, HUMAN, full_helm, [platebody], (sword_low, sword_up)),
    ("alchemist", 11, HUMAN, goggles, [belt_buckle], (flask_low, flask_up)),
    ("herald", 12, HUMAN, feathered_cap, [lambda g: cape(g, "left")], (bell_low, bell_up)),
    ("arbiter", 12, HUMAN, wig, [robe, lambda g: cape(g, "left")], (scales_low, scales_up)),
    ("scholar", 12, HUMAN, mortarboard, [lambda g: beard(g, 5), robe], (book_low, book_up)),
    ("oracle", 11, HUMAN, lambda g: hood(g, beard=True), [robe], (orb_low, orb_up)),
    ("engineer", 11, DWARF, mining_helmet, [lambda g: beard(g, 6), belt_buckle], (pick_low, pick_up)),
    ("rogue", 10, HUMAN, bandana, [belt_buckle], (dagger_low, dagger_up)),
    ("bard", 10, HUMAN, partyhat, [lambda g: cape(g, "left")], (lute_low, lute_up)),
    ("golem", 11, DWARF, stone_head, [lambda g: (g.put(6, 9, "o"), g.put(9, 11, "o"))], (rock_low, rock_up)),
]


def draw(body, head, extras, gear):
    out = {}
    low, up = gear
    for state in ("idle", "tool", "done", "failed"):
        g = G(normalise(body[state]))
        if head:
            head(g)
        for extra in extras:
            extra(g)
        if state == "tool":
            up(g)
        elif state in ("idle", "failed"):
            # lowered gear sits one column further out, beside the arm
            tmp = G()
            low(tmp)
            for y, row in enumerate(tmp.g):
                for x, c in enumerate(row):
                    if c != "." and x + 1 < W and (x >= 12 or c == "f"):
                        g.put(x + 1, y, c)
                    elif c != "." and x < 12:
                        g.put(x, y, c)
        out[state] = g.rows()
    return out


def emit():
    out = []
    for name, spark, body, head, extras, gear in AVATARS:
        frames = draw(body, head, extras, gear)
        out.append(f"const {name} = figure({spark}, {{")
        for state in ("idle", "tool", "done", "failed"):
            out.append(f"  {state}: [")
            for row in frames[state]:
                assert len(row) == 16 and set(row) <= set(".ofhe"), (name, state, row)
                out.append(f'    "{row}",')
            out.append("  ],")
        out.append("});\n")
    return "\n".join(out)


def splice(path):
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
