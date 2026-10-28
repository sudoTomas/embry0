/**
 * The named colours of the adventurer roster. Every hex was picked by eye:
 * the gear tiers read as a medal ladder (bronze warm brown, silver grey,
 * gold yellow, platinum pale blue-grey, diamond ice blue, obsidian violet
 * black, crimson red, divine pale gold); nothing is sampled from any game,
 * wiki or asset. Colours are fixed per part and per item; the cape and the
 * paper crown alone wear the party colour token.
 *
 * Imports inside appearance/ carry the .ts extension because
 * scripts/adventurer_sheet.mts runs this module under Node without a
 * bundler, where a bare "./palette" does not resolve.
 */
import type { ColourId, SkinTone } from "./types.ts";

/** "#rrggbb", or for the cape a CSS colour expression over --sprite-fill. */
export type Hex = string;

export const OUTLINE: Hex = "#15121c";

export const SKIN: Record<SkinTone, Hex> = {
  light: "#f0c8a0",
  tan: "#d49a66",
  brown: "#9b6a3e",
  dark: "#5a3a22",
  stone: "#8d8b83",
};

export const HAIR = {
  black: "#23202b",
  brown: "#5c3a1d",
  blonde: "#dcb44a",
  auburn: "#a63a1e",
  grey: "#9a9aa2",
  white: "#ebe9e4",
} as const;

export const CLOTH = {
  cream: "#d8ccaa",
  umber: "#6e4a28",
  green: "#3d7a3a",
  blue: "#2f4f9e",
  red: "#9e2b2b",
  purple: "#5a2d7a",
  charcoal: "#2b2a30",
  teal: "#2a8a86",
} as const;

/** One vocabulary for the four colour slots: a hair colour is a valid trouser colour and the reverse. */
export const COLOURS: Record<ColourId, Hex> = { ...HAIR, ...CLOTH };

export const METAL = {
  bronze: "#9c6b2f",
  silver: "#a9adb6",
  gold: "#d4a020",
  black: "#2c2b33",
  platinum: "#c7d3dc",
  diamond: "#7cc6dc",
  obsidian: "#3b2f52",
  crimson: "#b3261e",
  divine: "#e2c766",
} as const;
export type MetalTier = keyof typeof METAL;

export const LEATHER = {
  plain: "#8a5a2b",
  green: "#3f6e2d",
  blue: "#2f5f8f",
  red: "#8f2f2f",
  black: "#2a2a2e",
} as const;

export const MAGE = {
  novice: "#2b58c8",
  violet: "#8a3fb8",
  high: "#2a1e4d",
} as const;

/**
 * The ranged ladder's hides, Bronze to Divine: tanned leather, hardened,
 * studded, then scale in green, blue and red, and pale white hide.
 */
export const HIDE = {
  leather: "#8a5a2b",
  hard: "#4f3119",
  studded: "#7d5536",
  green: "#3f6e2d",
  blue: "#2f5f8f",
  red: "#8f2f2f",
  pale: "#e8e0c8",
} as const;

/** The magic ladder's robes, Bronze to Divine, each with its trim. */
export const ROBE = {
  novice: { base: "#2b58c8", trim: "#7f9be6" },
  dark: { base: "#2b2a30", trim: "#9a9aa2" },
  enchanted: { base: "#24306e", trim: "#c8c8d0" },
  violet: { base: "#8a3fb8", trim: "#e3b94b" },
  tidal: { base: "#1f6f8f", trim: "#e6e6ea" },
  archmage: { base: "#2a1e4d", trim: "#e3b94b" },
  celestial: { base: "#efe6cf", trim: "#e3b94b" },
} as const;

export const GOLD: Hex = "#e3b94b";
export const WOOD: Hex = "#7a4a22";
export const STRING: Hex = "#e6dcc0";

/** The cape's two tones: the party colour and a darker mix of it, so it still reads as that team's cape. */
export const CAPE_FILL: Hex = "var(--sprite-fill)";
export const CAPE_SHADE: Hex = "color-mix(in srgb, var(--sprite-fill) 55%, black)";

function channel(hex: Hex, i: number): number {
  return parseInt(hex.slice(1 + 2 * i, 3 + 2 * i), 16);
}

/** `t` of the way from `a` to `b`, per channel, rounded. */
export function mix(a: Hex, b: Hex, t: number): Hex {
  const out = [0, 1, 2].map((i) => Math.round(channel(a, i) + (channel(b, i) - channel(a, i)) * t));
  return "#" + out.map((v) => v.toString(16).padStart(2, "0")).join("");
}

export function dark(hex: Hex): Hex {
  return mix(hex, "#000000", 0.38);
}

export function light(hex: Hex): Hex {
  return mix(hex, "#ffffff", 0.28);
}

/**
 * The colour key most layers draw over: `1` dark, `2` base, `3` light of
 * the base; `4` `5` `6` the same of the trim when there is one; `o` the
 * outline. A shape drawn once over these keys is every tier of itself.
 */
export function tones(base: Hex, trim?: Hex): Record<string, Hex> {
  const key: Record<string, Hex> = { "1": dark(base), "2": base, "3": light(base), o: OUTLINE };
  if (trim) Object.assign(key, { "4": dark(trim), "5": trim, "6": light(trim) });
  return key;
}
