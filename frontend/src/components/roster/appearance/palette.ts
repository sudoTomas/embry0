/**
 * The named colours of the adventurer roster. Every hex was picked by eye
 * for how a tier reads in an old-school MMO (bronze warm brown, iron grey,
 * steel light grey, mithril deep blue, adamant green, rune teal-blue, dragon
 * red, divine pale gold); nothing is sampled from any game, wiki or asset.
 * Colours are fixed per part and per item; the cape and the party hat alone
 * wear the party colour token.
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
  iron: "#6b6b70",
  steel: "#b9bcc4",
  black: "#2c2b33",
  mithril: "#2f4ea0",
  adamant: "#2f7a4a",
  rune: "#3f7fa8",
  dragon: "#b3261e",
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
  wizard: "#2b58c8",
  mystic: "#8a3fb8",
  high: "#2a1e4d",
} as const;

/**
 * The ranged ladder's hides, Bronze to Divine: tanned leather, hardened,
 * studded, then dragonhide in green, blue and red, and blessed white hide.
 */
export const HIDE = {
  leather: "#8a5a2b",
  hard: "#4f3119",
  studded: "#7d5536",
  green: "#3f6e2d",
  blue: "#2f5f8f",
  red: "#8f2f2f",
  blessed: "#e8e0c8",
} as const;

/** The magic ladder's robes, Bronze to Divine, each with its trim. */
export const ROBE = {
  wizard: { base: "#2b58c8", trim: "#7f9be6" },
  dark: { base: "#2b2a30", trim: "#9a9aa2" },
  enchanted: { base: "#24306e", trim: "#c8c8d0" },
  mystic: { base: "#8a3fb8", trim: "#e3b94b" },
  astral: { base: "#1f6f8f", trim: "#e6e6ea" },
  archmage: { base: "#2a1e4d", trim: "#e3b94b" },
  celestial: { base: "#efe6cf", trim: "#e3b94b" },
} as const;

export const GOLD: Hex = "#e3b94b";
export const WOOD: Hex = "#7a4a22";
export const STRING: Hex = "#e6dcc0";

/** The cape's two tones: the party colour and a darker mix of it, so it still reads as that team's cape. */
export const CAPE_FILL: Hex = "var(--sprite-fill)";
export const CAPE_SHADE: Hex = "color-mix(in srgb, var(--sprite-fill) 55%, black)";

/**
 * A skill cape's body and trim, keyed by the classic skill a development
 * skill echoes (progression spec 2026-10-24). Picked by eye for what the
 * skill evokes: forge orange on iron for smithing, a red cross on white for
 * hitpoints; nothing is sampled from any game.
 */
export const SKILL_CAPE = {
  attack: { base: "#7a2a2a", trim: "#e3b94b" },
  strength: { base: "#2f6a3a", trim: "#c0392b" },
  defence: { base: "#2f4f9e", trim: "#d8ccaa" },
  ranged: { base: "#3a6a2a", trim: "#c9a86a" },
  prayer: { base: "#efe9da", trim: "#e3b94b" },
  magic: { base: "#3a2f7a", trim: "#7fb0ff" },
  runecraft: { base: "#c9a227", trim: "#2a2a2e" },
  construction: { base: "#8a6a4a", trim: "#d8ccaa" },
  hitpoints: { base: "#d8d8d8", trim: "#c0392b" },
  agility: { base: "#2a3550", trim: "#9a9aa2" },
  herblore: { base: "#2f7a4a", trim: "#d8ccaa" },
  thieving: { base: "#3a2a4a", trim: "#9a9aa2" },
  crafting: { base: "#7a5a3a", trim: "#e3b94b" },
  fletching: { base: "#2a6a6a", trim: "#c9a86a" },
  slayer: { base: "#2b2a30", trim: "#9e2b2b" },
  hunter: { base: "#5c4a2a", trim: "#3f6e2d" },
  mining: { base: "#3a3f55", trim: "#6fb7c9" },
  smithing: { base: "#4a4a52", trim: "#e07b2a" },
  fishing: { base: "#2f6f9e", trim: "#d8ccaa" },
  cooking: { base: "#e8e2d0", trim: "#9e2b2b" },
  firemaking: { base: "#c8641e", trim: "#f2c14e" },
  woodcutting: { base: "#3f6b2f", trim: "#8a5a2b" },
  farming: { base: "#4a7a2a", trim: "#c9a227" },
} as const;
export type Echo = keyof typeof SKILL_CAPE;

/** The quest cape: every quest the account offers, done. */
export const QUEST_CAPE = { base: "#2f4a8f", trim: "#ece6d4" } as const;

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
