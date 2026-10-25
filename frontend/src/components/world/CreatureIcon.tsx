import type { CSSProperties } from "react";
import { assetUrl, useWorldManifest } from "./manifest";

/**
 * What stands in the world for a lead: the art manifest's sheet when it has
 * one, else a small original pixel glyph. Glyphs are 12 by 12 text grids, a
 * letter per colour, drawn as SVG rectangles like the roster's sprites.
 */
const GLYPHS: Record<string, { rows: string[]; colours: Record<string, string> }> = {
  // A squat green creature with a club: a production error.
  creature: {
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
  "quest-giver": {
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
  signpost: {
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
  golem: {
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
  wyrm: {
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
  imp: {
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
  ghost: {
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

/** One lead's creature at ``size`` pixels: the manifest's art, else the glyph. */
export function CreatureIcon({ creature, size = 32, label }: { creature: string; size?: number; label?: string }) {
  const manifest = useWorldManifest();
  const asset = manifest.creatures[creature];
  return (
    <span role="img" aria-label={label ?? creature} className="inline-flex shrink-0" data-creature={creature} style={{ width: size, height: size }}>
      {asset ? (
        <span
          className="block"
          style={
            {
              width: size,
              height: size,
              backgroundImage: `url(${assetUrl(asset)})`,
              backgroundSize: `${(asset.frames ?? 1) * size}px ${size}px`,
              imageRendering: "pixelated",
            } as CSSProperties
          }
        />
      ) : (
        <Glyph creature={creature} size={size} />
      )}
    </span>
  );
}
