import { useQuery } from "@tanstack/react-query";
import { fetchTraces } from "@/api/traces";
import type { TraceFilters } from "@/lib/types";

export function useTraces(filters: TraceFilters = {}) {
  return useQuery({
    queryKey: ["traces", filters],
    queryFn: () => fetchTraces(filters),
  });
}
