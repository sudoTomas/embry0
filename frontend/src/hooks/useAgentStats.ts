import { useQuery } from "@tanstack/react-query";
import { fetchStats as fetchAgentStats } from "@/api/agent";

export function useAgentStats() {
  return useQuery({
    queryKey: ["agent-stats"],
    queryFn: fetchAgentStats,
    refetchInterval: 30_000,
  });
}
