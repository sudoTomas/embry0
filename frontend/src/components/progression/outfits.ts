import { createContext, useContext } from "react";
import type { Character, Skill } from "@/api/progression";
import { RARES, type Outfit, type Rare } from "@/components/roster";
import { SKILL_CAPE, type Echo } from "@/components/roster/appearance/palette";

/** A character's outfit (progression spec 2026-10-24): its tier, its 99s as cape echoes, its rares. */
export function outfitOf(character: Character, skills: readonly Skill[]): Outfit {
  const echo = new Map(skills.map((s) => [s.id, s.echo]));
  return {
    tier: character.tier,
    capes: character.capes.map((stat) => echo.get(stat)).filter((e): e is Echo => e !== undefined && e in SKILL_CAPE),
    rares: character.rares.map((r) => r.id).filter((id): id is Rare => (RARES as readonly string[]).includes(id)),
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
