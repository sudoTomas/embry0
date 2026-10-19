import { useCallback, useMemo } from "react";
import { useAgents } from "@/hooks/useAgents";
import { identityOf, resolveAgentType, type AgentIdentity, type IdentitySource } from "@/lib/agentIdentity";

function lookup(rows: IdentitySource[] | undefined, type: string): AgentIdentity {
  const resolved = resolveAgentType(type);
  return identityOf(rows?.find((a) => a.type === resolved) ?? resolved);
}

/** Identity for one agent type, from the cached GET /agents rows; builtin/default fallbacks when absent. */
export function useAgentIdentity(type: string | null | undefined): AgentIdentity {
  const { data } = useAgents();
  return useMemo(() => lookup(data as IdentitySource[] | undefined, type ?? ""), [data, type]);
}

/** A lookup for lists: one subscription, many types. */
export function useIdentityLookup(): (type: string) => AgentIdentity {
  const { data } = useAgents();
  return useCallback((type: string) => lookup(data as IdentitySource[] | undefined, type), [data]);
}
