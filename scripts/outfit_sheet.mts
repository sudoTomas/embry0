#!/usr/bin/env node
/**
 * The outfits' contact sheet (progression spec 2026-10-24): one character
 * per combat style up its ladder from Bronze to Divine, a styleless one
 * whose tool takes the tier's metal, skill capes plain and trimmed, and the
 * rares, at 96 px in the idle pose, on the layout of adventurer_sheet.mts.
 *
 *   node scripts/outfit_sheet.mts > docs/superpowers/plans/2026-10-24-outfits-contact-sheet.svg
 *
 * The party-colour tokens resolve to gold, since an SVG file has no --sprite-fill.
 */
import { POSES, compose } from "../frontend/src/components/roster/appearance/compose.ts";
import { GEAR_TIERS } from "../frontend/src/components/roster/appearance/items.ts";
import { STYLE, dress, type Outfit } from "../frontend/src/components/roster/appearance/outfit.ts";
import { PRESETS, type PresetId } from "../frontend/src/components/roster/appearance/presets.ts";
import type { Appearance } from "../frontend/src/components/roster/appearance/types.ts";

const PX = 96;
const CELL = PX / 32;
const PAD = 8;
const CARD = PX + PAD * 2;
const LABEL = 130;
const GAP = 6;
const TOP = 36;
const ROW = CARD + 24;
const PARTY = "#d4af37";

/** Every colour over the party token, as the sheet's one gold. */
const fillOf = (colour: string) => (colour.includes("var(--sprite-fill)") ? (colour.includes("black") ? "#7a6520" : PARTY) : colour);
const none: Outfit = { tier: "bronze", capes: [], rares: [] };
const worn = (id: PresetId, outfit: Partial<Outfit>) => dress(PRESETS[id], STYLE[id], { ...none, ...outfit });

const rows: { name: string; cells: [string, Appearance][] }[] = [
  ...(["smith", "herald", "seer", "skiller"] as PresetId[]).map((id) => ({
    name: `${id}: ${STYLE[id] ?? "own look"}`,
    cells: GEAR_TIERS.map((tier): [string, Appearance] => [tier, worn(id, { tier })]),
  })),
  {
    name: "skill capes",
    cells: [
      ["smithing", worn("smith", { tier: "rune", capes: ["smithing"] })],
      ["smithing (t)", worn("smith", { tier: "rune", capes: ["smithing", "mining"] })],
      ["defence", worn("warden", { tier: "rune", capes: ["defence"] })],
      ["slayer", worn("oracle", { tier: "rune", capes: ["slayer"] })],
      ["herblore", worn("alchemist", { tier: "rune", capes: ["herblore"] })],
      ["firemaking (t)", worn("engineer", { tier: "rune", capes: ["firemaking", "strength"] })],
    ],
  },
  {
    name: "rares",
    cells: [
      ["party hat", worn("smith", { tier: "adamant", rares: ["party-hat"] })],
      ["white party hat", worn("seer", { tier: "adamant", rares: ["white-party-hat"] })],
      ["holiday mask", worn("herald", { tier: "adamant", rares: ["holiday-mask"] })],
    ],
  },
];

const columns = Math.max(...rows.map((r) => r.cells.length));
const width = LABEL + columns * (CARD + GAP) + 16;
const height = TOP + rows.length * ROW + 8;

const out: string[] = [];
out.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" font-family="system-ui, sans-serif" font-size="12">`);
out.push(`<rect width="100%" height="100%" fill="#060d12"/>`);
out.push(`<text x="16" y="22" fill="#fafafa" font-size="14" font-weight="600">Outfits: each ladder Bronze to Divine, skill capes, rares (${PX} px; party colour in gold)</text>`);

rows.forEach((row, r) => {
  const y = TOP + r * ROW;
  out.push(`<text x="16" y="${y + CARD / 2 + 4}" fill="#fafafa" font-weight="600">${row.name}</text>`);
  row.cells.forEach(([label, appearance], c) => {
    const x = LABEL + c * (CARD + GAP);
    const frame = compose(appearance, POSES.idle[0]);
    out.push(`<rect x="${x}" y="${y}" width="${CARD}" height="${CARD}" rx="6" fill="#0f1419"/>`);
    const paths = Object.entries(frame.paths)
      .map(([colour, d]) => `<path d="${d}" fill="${fillOf(colour)}"/>`)
      .join("");
    out.push(`<g transform="translate(${x + PAD} ${y + PAD}) scale(${CELL})" shape-rendering="crispEdges">${paths}</g>`);
    out.push(`<text x="${x + CARD / 2}" y="${y + CARD + 16}" fill="#a1a1aa" text-anchor="middle">${label}</text>`);
  });
});
out.push("</svg>");
process.stdout.write(out.join("\n") + "\n");
