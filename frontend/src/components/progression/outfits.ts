import { createContext, useContext } from "react";
import type { Character } from "@/api/progression";
import { RARE_ART, type Outfit, type RareArt } from "@/components/roster";
import { GEAR_TIERS, type GearTier } from "@/components/roster/appearance/items";
import type { GamePack } from "@/lib/gamePack";

/**
 * A character's outfit (progression spec 2026-10-24), resolved through the
 * active content pack (#176): the gear art step of its tier, its best top-level
 * skill's cloak (trimmed for two or more), the quest cloak, the first rare in
 * the pack's wear order that it holds and the dashboard can draw, and the
 * account's cloak trim.
 */
export function outfitOf(character: Character, pack: GamePack, divineTrim = false): Outfit {
  const gear = (GEAR_TIERS as readonly string[]).includes(character.gear) ? (character.gear as GearTier) : "bronze";
  const [best] = character.capes;
  const held = new Set(character.rares.map((r) => r.id));
  const rare = pack.rares.find((r) => held.has(r.id) && r.art !== null && (RARE_ART as readonly string[]).includes(r.art));
  return {
    tier: gear,
    cloak: best ? (pack.skillById.get(best)?.cloak ?? null) : null,
    trimmed: character.capes.length > 1,
    questCloak: character.quest_cloak ? pack.questCloak : null,
    head: (rare?.art as RareArt | undefined) ?? null,
    divineTrim,
  };
}

export type OutfitLookup = (agentType: string | null | undefined) => Outfit | null;

/** Without a provider (a page under test, the agent form's preview), avatars wear no outfit. */
export const OutfitContext = createContext<OutfitLookup>(() => null);

/** A lookup for lists: one subscription, many agent types. */
export function useOutfitLookup(): OutfitLookup {
  return useContext(OutfitContext);
}

export function useOutfit(agentType: string | null | undefined): Outfit | null {
  return useContext(OutfitContext)(agentType);
}
