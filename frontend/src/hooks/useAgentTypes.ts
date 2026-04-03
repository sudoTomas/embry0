import { useQuery } from "@tanstack/react-query";
import { fetchAgentTypes } from "@/api/agents";

export function useAgentTypes() {
  return useQuery({
    queryKey: ["agent-types"],
    queryFn: fetchAgentTypes,
    staleTime: 5 * 60 * 1000, // agent types rarely change
  });
}
