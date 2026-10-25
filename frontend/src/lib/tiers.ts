import type { AchievementTier, Tier } from "@/api/progression";
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

/** The seven tiers, Bronze first, as an achievement's thresholds run. */
export const TIER_ORDER: readonly Tier[] = ["bronze", "iron", "steel", "mithril", "adamant", "rune", "divine"];

/** What a complete objective reads as, and its colour: parchment, outside the metal ladder. */
export const COMPLETE_NAME = "Complete";
export const COMPLETE_COLOR = "#d8ccaa";

/** An achievement tier's colour; a tierless entry draws in a muted slate. */
export function achievementColor(tier: AchievementTier | null): string {
  if (tier === null) return "#55525e";
  return tier === "complete" ? COMPLETE_COLOR : TIER_COLOR[tier];
}

/** An achievement tier's name in words. */
export function achievementTierName(tier: AchievementTier): string {
  return tier === "complete" ? COMPLETE_NAME : TIER_NAME[tier];
}

/** A colour lightened enough to read as text on the dark panels (mithril and dragon blues are dark). */
export function readable(color: string): string {
  return `color-mix(in srgb, ${color} 62%, white)`;
}
