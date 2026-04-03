import { useQuery } from "@tanstack/react-query";
import { fetchStats } from "@/api/stats";

export function useStats() {
  return useQuery({
    queryKey: ["stats"],
    queryFn: fetchStats,
    refetchInterval: 30_000,
  });
}
