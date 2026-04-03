import { useQuery } from "@tanstack/react-query";
import { fetchAgentTypes } from "@/api/agents";
import type { AgentTypeInfo } from "@/lib/types";

export function useAgentTypes() {
  return useQuery<AgentTypeInfo[]>({
    queryKey: ["agent-types"],
    queryFn: fetchAgentTypes,
    staleTime: 10 * 60 * 1000,
  });
}
