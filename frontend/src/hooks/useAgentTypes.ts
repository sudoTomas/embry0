import { useQuery } from "@tanstack/react-query";
import { fetchAgentTypes } from "@/api/agents";
import type { AgentDefinition } from "@/lib/types";

export function useAgentTypes() {
  return useQuery<AgentDefinition[]>({
    queryKey: ["agent-types"],
    queryFn: fetchAgentTypes,
    staleTime: 10 * 60 * 1000,
  });
}
