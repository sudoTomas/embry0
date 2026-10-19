import { useCallback, useMemo } from "react";
import { useAgents } from "@/hooks/useAgents";
import { identityOf, type AgentIdentity, type IdentitySource } from "@/lib/agentIdentity";

/** Identity for one agent type, from the cached GET /agents rows; builtin/default fallbacks when absent. */
export function useAgentIdentity(type: string | null | undefined): AgentIdentity {
  const { data } = useAgents();
  return useMemo(() => {
    const row = (data as IdentitySource[] | undefined)?.find((a) => a.type === type);
    return identityOf(row ?? (type ?? ""));
  }, [data, type]);
}

/** A lookup for lists: one subscription, many types. */
export function useIdentityLookup(): (type: string) => AgentIdentity {
  const { data } = useAgents();
  return useCallback(
    (type: string) => identityOf((data as IdentitySource[] | undefined)?.find((a) => a.type === type) ?? type),
    [data],
  );
}
