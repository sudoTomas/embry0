import { useMemo, type ReactNode } from "react";
import { useProgression } from "@/hooks/useProgression";
import { resolveAgentType } from "@/lib/agentIdentity";
import { OutfitContext, outfitOf, type OutfitLookup } from "./outfits";

/** Reads progression once for the whole app, so every sprite of a character wears what it earned. */
export function OutfitProvider({ children }: { children: ReactNode }) {
  const { data } = useProgression();
  const lookup = useMemo<OutfitLookup>(() => {
    if (!data) return () => null;
    const outfits = new Map(data.characters.map((c) => [c.agent_type, outfitOf(c, data.account.divine_trim)]));
    return (agentType) => (agentType ? outfits.get(resolveAgentType(agentType)) ?? null : null);
  }, [data]);
  return <OutfitContext.Provider value={lookup}>{children}</OutfitContext.Provider>;
}
