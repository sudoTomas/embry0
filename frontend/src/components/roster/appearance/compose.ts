/**
 * Renders an appearance: the body-kit parts and the worn items are laid on
 * the grid in draw order (cape, legs, feet, body, arms with hands, head,
 * hair, beard, head slot, neck, shield, weapon), then the cells are grouped
 * by colour into one SVG path per colour: run-length rectangles over any
 * grid and any colour key.
 *
 * A pose moves the arm and weapon layers and sets the face; everything
 * else stays put. POSES gives every sprite state its frames.
 */
import type { SpriteState } from "../sprites.ts";
import { itemOf, type Item } from "./items.ts";
import { COLOURS, SKIN, type Hex } from "./palette.ts";
import { arm, beard, feet, hair, head, legs, torso } from "./parts.ts";
import { GRID, type Appearance, type ArmPose, type EquipmentSlot, type Face, type Frame, type Layer } from "./types.ts";

export interface Pose {
  arms: { left: ArmPose; right: ArmPose };
  face: Face;
  /** Shift the whole figure up one row: idle frame 1. */
  bob: boolean;
  /** Column of the one-cell spark on row 0, or null. */
  spark: number | null;
  /** A sweat drop beside the head. */
  sweat: boolean;
  /** The outline alone, in the dim tint: the pending state. */
  ghost: boolean;
}

export interface NamedLayer extends Layer {
  name: "cape" | "legs" | "feet" | "body" | "arm-left" | "arm-right" | "shoulders" | "head" | "hair" | "beard" | "head-item" | "neck" | "shield" | "weapon";
}

export interface ComposedFrame {
  size: number;
  /** Colour to path, in draw order of first appearance. */
  paths: Record<Hex, string>;
}

export const SPARK: Hex = "#fff3b0";
export const SWEAT: Hex = "#9fd8ff";
/** The pending frame is outline only; in the background colour it vanishes on every surface, so it is a dim tint instead (Sprite.tsx). */
export const GHOST_FILL: Hex = "color-mix(in srgb, var(--sprite-fill) 45%, transparent)";
/** Rows the shield rises with the right arm in the done pose. */
const SHIELD_RAISE = 4;
/** Column of the thinking spark: above and right of the head. */
const SPARK_AT = 20;

const STAND: Pose = { arms: { left: "down", right: "down" }, face: "open", bob: false, spark: null, sweat: false, ghost: false };

export const POSES: Record<SpriteState, Pose[]> = {
  idle: [STAND, { ...STAND, bob: true }],
  thinking: [{ ...STAND, face: "closed", spark: SPARK_AT }, { ...STAND, spark: SPARK_AT + 1 }],
  tool: [{ ...STAND, arms: { left: "up", right: "down" } }, STAND],
  done: [{ ...STAND, arms: { left: "up", right: "up" }, face: "smile" }],
  failed: [{ ...STAND, arms: { left: "slump", right: "slump" }, face: "frown", sweat: true }],
  pending: [{ ...STAND, ghost: true }],
};

function worn(a: Appearance, slot: EquipmentSlot): Item | undefined {
  const id = a.equipment[slot];
  return id === undefined ? undefined : itemOf(id);
}

function shifted(f: Frame, dy: number): Frame {
  return { ...f, y: f.y + dy };
}

/** The layers of one appearance in one pose, in draw order. */
export function layersOf(a: Appearance, pose: Pose): NamedLayer[] {
  const out: NamedLayer[] = [];
  const put = (name: NamedLayer["name"], layer: Layer | undefined) => { if (layer) out.push({ name, ...layer }); };
  const itemLayer = (item: Item | undefined, frame = item?.layer): Layer | undefined =>
    item && frame ? { frame, colours: item.colours } : undefined;

  const body = worn(a, "body");
  const headItem = worn(a, "head");
  const hands = worn(a, "hands");
  const shield = worn(a, "shield");
  const weapon = worn(a, "weapon");

  put("cape", itemLayer(worn(a, "cape")));
  put("legs", legs(a.colours.legs));
  put("legs", itemLayer(worn(a, "legs")));
  put("feet", feet(a.colours.feet));
  put("feet", itemLayer(worn(a, "feet")));
  put("body", torso(a.colours.torso));
  put("body", itemLayer(body));
  const sleeve = body?.sleeves ?? COLOURS[a.colours.torso];
  const hand = hands?.hands ?? SKIN[a.skin];
  put("arm-left", arm("left", pose.arms.left, sleeve, hand));
  put("arm-right", arm("right", pose.arms.right, sleeve, hand));
  put("shoulders", itemLayer(body, body?.shoulders));
  const cape = worn(a, "cape");
  if (cape?.drape) put("shoulders", itemLayer(cape, cape.drape));
  put("head", head(a.skin, pose.face));
  if (!headItem?.over) put("hair", hair(a.hair, a.colours.hair));
  if (a.beard && headItem?.over !== "beard") put("beard", beard(a.beard, a.colours.hair));
  put("head-item", itemLayer(headItem));
  put("neck", itemLayer(worn(a, "neck")));
  if (shield?.layer) put("shield", itemLayer(shield, pose.arms.right === "up" ? shifted(shield.layer, -SHIELD_RAISE) : shield.layer));
  if (weapon?.weapon) put("weapon", itemLayer(weapon, pose.arms.left === "up" ? weapon.weapon.raised : weapon.weapon.rest));
  return out;
}

export type Grid = (Hex | null)[][];

function blank(): Grid {
  return Array.from({ length: GRID }, () => Array<Hex | null>(GRID).fill(null));
}

function place(grid: Grid, layer: NamedLayer, dy: number, lift: (x: number) => number = () => 0): void {
  layer.frame.rows.forEach((row, j) => {
    for (let i = 0; i < row.length; i++) {
      const c = row[i];
      if (c === ".") continue;
      const x = layer.frame.x + i;
      if (x < 0 || x >= GRID) continue;
      const y = layer.frame.y + j + dy + lift(x);
      if (y < 0 || y >= GRID) continue;
      if (c === "x") { grid[y][x] = null; continue; }
      const colour = layer.colours[c];
      if (colour === undefined) throw new Error(`appearance: layer ${layer.name} has no colour for ${JSON.stringify(c)}`);
      grid[y][x] = colour;
    }
  });
}

/** The silhouette's edge: every opaque cell with a transparent four-neighbour or a grid edge. */
function ghostOf(grid: Grid): Grid {
  const out = blank();
  const opaque = (x: number, y: number) => x >= 0 && y >= 0 && x < GRID && y < GRID && grid[y][x] !== null;
  for (let y = 0; y < GRID; y++) {
    for (let x = 0; x < GRID; x++) {
      if (!opaque(x, y)) continue;
      if (!opaque(x - 1, y) || !opaque(x + 1, y) || !opaque(x, y - 1) || !opaque(x, y + 1)) out[y][x] = GHOST_FILL;
    }
  }
  return out;
}

/** The 32 by 32 colour grid of one appearance in one pose. */
export function paint(a: Appearance, pose: Pose): Grid {
  const grid = blank();
  const dy = pose.bob ? -1 : 0;
  for (const layer of layersOf(a, pose)) place(grid, layer, dy);
  if (pose.sweat) { grid[4][22] = SWEAT; grid[5][22] = SWEAT; grid[5][21] = SWEAT; }
  if (pose.spark !== null) grid[0][pose.spark] = SPARK;
  return pose.ghost ? ghostOf(grid) : grid;
}

/**
 * Run-length rectangles, one path per colour: "M<x> <y>h<len>v<rows>h-<len>z"
 * per run, a run extending down through every row below that repeats it
 * exactly, so a torso is one rectangle rather than nine.
 */
export function runLength(grid: Grid): Record<Hex, string> {
  const runs: Record<Hex, string[]> = {};
  const taken = grid.map((row) => row.map(() => false));
  grid.forEach((row, y) => {
    let x = 0;
    while (x < row.length) {
      const c = row[x];
      if (c === null || taken[y][x]) { x++; continue; }
      let len = 1;
      while (x + len < row.length && row[x + len] === c && !taken[y][x + len]) len++;
      const same = (yy: number) => yy < grid.length
        && (x === 0 || grid[yy][x - 1] !== c) && (x + len >= row.length || grid[yy][x + len] !== c)
        && grid[yy].slice(x, x + len).every((cell, i) => cell === c && !taken[yy][x + i]);
      let rows = 1;
      while (same(y + rows)) rows++;
      for (let yy = y; yy < y + rows; yy++) for (let i = 0; i < len; i++) taken[yy][x + i] = true;
      (runs[c] ??= []).push(`M${x} ${y}h${len}v${rows}h-${len}z`);
      x += len;
    }
  });
  const paths: Record<Hex, string> = {};
  for (const [colour, segs] of Object.entries(runs)) paths[colour] = segs.join("");
  return paths;
}

export function compose(a: Appearance, pose: Pose): ComposedFrame {
  return { size: GRID, paths: runLength(paint(a, pose)) };
}

/**
 * Two walking frames (the world, spec 2026-10-25): the left leg lifted a row,
 * then the right, the rest of the figure as it stands, so a character walks
 * in the gear it earned.
 */
export function walkFrames(a: Appearance): ComposedFrame[] {
  const layers = layersOf(a, STAND);
  return [0, 1].map((step) => {
    const grid = blank();
    const lifted = (x: number) => ((x < GRID / 2) === (step === 0) ? -1 : 0);
    for (const layer of layers) place(grid, layer, 0, layer.name === "legs" || layer.name === "feet" ? lifted : undefined);
    return { size: GRID, paths: runLength(grid) };
  });
}

/** Every frame of one state: two for idle, thinking and tool, one otherwise. */
export function composeState(a: Appearance, state: SpriteState): ComposedFrame[] {
  return POSES[state].map((pose) => compose(a, pose));
}
