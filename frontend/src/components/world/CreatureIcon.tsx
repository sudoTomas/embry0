import { ArtSprite } from "./art/ArtSprite";
import { creatureFor, useArt } from "./art/manifest";

/**
 * What stands in the world for a lead: the art manifest's sheet when it has
 * one, else a small original pixel glyph. Glyphs are 12 by 12 text grids, a
 * letter per colour, drawn as SVG rectangles like the roster's sprites.
 */
const GLYPHS: Record<string, { rows: string[]; colours: Record<string, string> }> = {
  // A squat green creature with a club: a production error.
  case_bug: {
    rows: [
      "............",
      "...gggg.....",
      "..gwgwgg....",
      "..gggggg..b.",
      "...gkkg...b.",
      "..gggggggbb.",
      ".gg.gggg.b..",
      ".g..gggg....",
      "....gggg....",
      "...gg..gg...",
      "...g....g...",
      "..kk....kk..",
    ],
    colours: { g: "#5aa02c", w: "#ffffff", k: "#2b1d0e", b: "#8b5a2b" },
  },
  // A figure with a gold exclamation mark: someone with a task.
  quest_giver: {
    rows: [
      ".....y......",
      ".....y......",
      ".....y......",
      "............",
      ".....y......",
      "....ssss....",
      "....sdds....",
      "...cccccc...",
      "..ccccccccc.",
      "...cccccc...",
      "...cc..cc...",
      "...kk..kk...",
    ],
    colours: { y: "#ffcf3f", s: "#e0b48a", d: "#2b1d0e", c: "#3a6ea5", k: "#2b1d0e" },
  },
  // A crumbling signpost: an instruction that steers wrong.
  audit_signpost: {
    rows: [
      "............",
      ".wwwwwwww...",
      ".wkkkkkkww..",
      ".wwwwwwww...",
      "....bb......",
      ".wwwwwww....",
      "..wkkkkw....",
      ".wwwwwww....",
      "....bb......",
      "....bb......",
      "...bbbb.....",
      "..gggggg....",
    ],
    colours: { w: "#c9a46a", k: "#5b3b1a", b: "#7a5230", g: "#4f7f2a" },
  },
  // A stone golem across the road: a broken build.
  ci_golem: {
    rows: [
      "....rrrr....",
      "...rrrrrr...",
      "...rorror...",
      "..rrrrrrrr..",
      ".rrrrrrrrrr.",
      "rrr.rrrr.rrr",
      "rr..rrrr..rr",
      "....rrrr....",
      "...rr..rr...",
      "...rr..rr...",
      "..rrr..rrr..",
      "..rrr..rrr..",
    ],
    colours: { r: "#8d8a83", o: "#ff981f" },
  },
  // A red wyrm: a security alert.
  alert_wyrm: {
    rows: [
      "..rr........",
      ".rrrr.......",
      ".ryrr.......",
      "..rrrr..w...",
      "...rrrr.ww..",
      "....rrrrrww.",
      "...rrrrrrww.",
      "..rrrrrrrr..",
      ".rr.rrrrr...",
      ".r...rrrrr..",
      ".......rrrr.",
      ".........rr.",
    ],
    colours: { r: "#c0392b", y: "#ffcf3f", w: "#7d1f15" },
  },
  // A flickering imp: a flaky test.
  flaky_imp: {
    rows: [
      "..p......p..",
      "..pp....pp..",
      "...pppppp...",
      "...pyppyp...",
      "...pppppp...",
      "....pkkp....",
      "..pppppppp..",
      ".p.pppppp.p.",
      "...pppppp...",
      "...pp..pp...",
      "..pp....pp..",
      "............",
    ],
    colours: { p: "#9b59b6", y: "#fff3b0", k: "#2b1d0e" },
  },
  // A pale ghost with a question: an unanswered review comment.
  review_ghost: {
    rows: [
      "....wwww....",
      "...wwwwww...",
      "..wwkwwkww..",
      "..wwwwwwww..",
      "..wwwkkwww..",
      "..wwwwwwww..",
      "..wwwwwwww..",
      "..wwwwwwww..",
      "..wwwwwwww..",
      "..w.ww.ww.w.",
      "............",
      "............",
    ],
    colours: { w: "#dfe6e9", k: "#2d3436" },
  },
  // A dripping green ooze with two eyes: slop, not a bug (spec 2026-10-25, slop dungeon).
  slop_ooze: {
    rows: [
      "............",
      "............",
      "....gggg....",
      "...gggggg...",
      "..gwkggwkg..",
      "..gggggggg..",
      ".gggllgggg..",
      ".gggggggggg.",
      "gggggggggggg",
      "g.gg.ggg.g.g",
      "g..g..g....g",
      "...d......d.",
    ],
    colours: { g: "#7fb800", l: "#b6e35a", w: "#ffffff", k: "#1d2b06", d: "#5e8a00" },
  },
  // A clay pot with a gold seam down a crack: a crack the scout found, to mend (spec 2026-10-27).
  crack_pot: {
    rows: [
      "............",
      "....cccc....",
      "...c.cc.c...",
      "....cccc....",
      "...cccyccc..",
      "..cccyccccc.",
      "..ccccyyccc.",
      "..cccccyccc.",
      "..ccccycccc.",
      "...cccyccc..",
      "....ccccc...",
      "............",
    ],
    colours: { c: "#b5653a", y: "#f2c230" },
  },
  // A weed with two leaves on a clod of earth: dead code the scout found, to pull (spec 2026-10-27).
  weed_sprout: {
    rows: [
      "............",
      ".....g......",
      "....ggg.....",
      "..ll.g......",
      ".llll.g.....",
      "..lll.g.ll..",
      ".....g.llll.",
      ".....g.lll..",
      ".....g......",
      "...ddgddd...",
      "..dddddddd..",
      "............",
    ],
    colours: { g: "#3f7d1e", l: "#7fb800", d: "#6b4a2b" },
  },
  // A cave mouth with ooze running out of it: a Slop Dungeon on the map.
  slop_dungeon: {
    rows: [
      "....ssss....",
      "..ssssssss..",
      ".sssskksss..",
      ".sskkkkkkss.",
      "sskkkkkkkkss",
      "sskkkggkkkss",
      "sskkggggkkss",
      "sskggggggkss",
      "sssgggggggss",
      "ssggggggggss",
      ".gggggggggg.",
      "gggg.gg.gggg",
    ],
    colours: { s: "#6d6a63", k: "#141210", g: "#7fb800" },
  },
  mystery: {
    rows: [
      "....yyyy....",
      "...yy..yy...",
      ".......yy...",
      "......yy....",
      ".....yy.....",
      ".....yy.....",
      "............",
      ".....yy.....",
      ".....yy.....",
      "............",
      "............",
      "............",
    ],
    colours: { y: "#ffcf3f" },
  },
};

function Glyph({ creature, size }: { creature: string; size: number }) {
  const glyph = GLYPHS[creature] ?? GLYPHS.mystery;
  return (
    <svg viewBox="0 0 12 12" width={size} height={size} shapeRendering="crispEdges" aria-hidden="true">
      {glyph.rows.flatMap((row, y) =>
        [...row].map((cell, x) =>
          cell === "." ? null : <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={glyph.colours[cell] ?? "#fff"} />,
        ),
      )}
    </svg>
  );
}

/**
 * A lead's creature in a box of ``size`` pixels: the art manifest's creature
 * for its kind, its idle loop stepping, at a whole-number scale (a creature
 * larger than the box is drawn at half); the glyph when the art has none.
 */
export function CreatureIcon({ kind, creature, size = 32, label }: { kind: string; creature: string; size?: number; label?: string }) {
  const art = useArt();
  const found = creatureFor(art, kind);
  const atlas = found ? art.atlases[found[1].atlas] : undefined;
  if (!found || !atlas) {
    return (
      <span role="img" aria-label={label ?? kind} className="inline-flex shrink-0" data-creature={creature} style={{ width: size, height: size }}>
        <Glyph creature={creature} size={size} />
      </span>
    );
  }
  const [name, c] = found;
  const scale = c.size <= size ? Math.max(1, Math.floor(size / c.size)) : 0.5;
  return (
    <span
      role="img"
      aria-label={label ?? kind}
      className="inline-flex shrink-0 items-end justify-center overflow-hidden"
      data-creature={name}
      style={{ width: size, height: size }}
    >
      <ArtSprite image={atlas.image} rect={c.frames[0]} frames={c.frames} animation={`creature-${name}`} fps={c.fps} loop={c.loop} scale={scale} />
    </span>
  );
}
