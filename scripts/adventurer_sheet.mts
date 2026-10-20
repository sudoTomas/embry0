#!/usr/bin/env node
/**
 * The adventurer roster's contact sheet: every preset in every state, both
 * frames of a two-frame state, at 96 px, rendered from the composed frames
 * into one SVG. One row per preset, a label column, dark background, cells
 * on cards: the layout of the first roster's sheet.
 *
 *   node scripts/adventurer_sheet.mts > docs/superpowers/plans/2026-10-20-adventurer-roster-contact-sheet.svg
 *
 * Node 22 strips the types itself; the appearance module's imports carry
 * .ts extensions for that reason. The cape's party-colour token is
 * resolved here to the sheet's one colour, and the pending tint to a grey,
 * since an SVG file has no --sprite-fill.
 */
import { PRESETS } from "../frontend/src/components/roster/appearance/presets.ts";
import { GHOST_FILL, composeState } from "../frontend/src/components/roster/appearance/compose.ts";
import { CAPE_FILL, CAPE_SHADE } from "../frontend/src/components/roster/appearance/palette.ts";
import { FRAME_COUNTS, SPRITE_STATES } from "../frontend/src/components/roster/sprites.ts";

const PX = 96;
const CELL = PX / 32;
const PAD = 8;
const CARD = PX + PAD * 2;
const LABEL = 118;
const GAP = 6;
const TOP = 36;
const ROW = CARD + 24;
const CAPE = "#d4af37";

const fillOf: Record<string, string> = {
  [CAPE_FILL]: CAPE,
  [CAPE_SHADE]: "#7a6520",
  [GHOST_FILL]: "#4b5563",
};

const names = Object.keys(PRESETS);
const columns = SPRITE_STATES.reduce((n, s) => n + FRAME_COUNTS[s], 0);
const width = LABEL + columns * (CARD + GAP) + 16;
const height = TOP + names.length * ROW + 8;

const out: string[] = [];
out.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" font-family="system-ui, sans-serif" font-size="12">`);
out.push(`<rect width="100%" height="100%" fill="#060d12"/>`);
out.push(`<text x="16" y="22" fill="#fafafa" font-size="14" font-weight="600">Adventurers: every preset, every state, both frames (${PX} px; cape in gold)</text>`);

names.forEach((name, r) => {
  const y = TOP + r * ROW;
  out.push(`<text x="16" y="${y + CARD / 2 + 4}" fill="#fafafa" font-weight="600">${name}</text>`);
  let c = 0;
  for (const state of SPRITE_STATES) {
    composeState(PRESETS[name], state).forEach((frame, i) => {
      const x = LABEL + c * (CARD + GAP);
      out.push(`<rect x="${x}" y="${y}" width="${CARD}" height="${CARD}" rx="6" fill="#0f1419"/>`);
      const paths = Object.entries(frame.paths)
        .map(([colour, d]) => `<path d="${d}" fill="${fillOf[colour] ?? colour}"/>`)
        .join("");
      out.push(`<g transform="translate(${x + PAD} ${y + PAD}) scale(${CELL})" shape-rendering="crispEdges">${paths}</g>`);
      out.push(`<text x="${x + CARD / 2}" y="${y + CARD + 16}" fill="#a1a1aa" text-anchor="middle">${state} ${i}</text>`);
      c++;
    });
  }
});
out.push("</svg>");
process.stdout.write(out.join("\n") + "\n");
