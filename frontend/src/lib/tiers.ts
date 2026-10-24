import type { Tier } from "@/api/progression";
import { METAL } from "@/components/roster/appearance/palette";

/** A tier's colour: the roster's metal for the six metals, the gold palette token for Divine. */
export const TIER_COLOR: Record<Tier, string> = {
  bronze: METAL.bronze,
  iron: METAL.iron,
  steel: METAL.steel,
  mithril: METAL.mithril,
  adamant: METAL.adamant,
  rune: METAL.rune,
  divine: "var(--color-palette-gold)",
};

export const TIER_NAME: Record<Tier, string> = {
  bronze: "Bronze", iron: "Iron", steel: "Steel", mithril: "Mithril", adamant: "Adamant", rune: "Rune", divine: "Divine",
};
