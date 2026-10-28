import { createContext, useContext } from "react";
import type { Character } from "@/api/progression";
import { RARES, type Outfit, type Rare } from "@/components/roster";
import { MASTERY_CLOAK, type SkillId } from "@/components/roster/appearance/palette";

/** A character's outfit (progression spec 2026-10-24): its tier, its skills at 99 as mastery cloaks, its rares, its quest cloak and the account's Divine trim. */
export function outfitOf(character: Character, divineTrim = false): Outfit {
  return {
    tier: character.tier,
    capes: character.capes.filter((stat): stat is SkillId => stat in MASTERY_CLOAK),
    rares: character.rares.map((r) => r.id).filter((id): id is Rare => (RARES as readonly string[]).includes(id)),
    questCloak: character.quest_cloak,
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
