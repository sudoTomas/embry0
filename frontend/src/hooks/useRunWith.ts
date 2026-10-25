import { useQuery } from "@tanstack/react-query";
import { fetchRunWithKeys } from "@/api/runWith";

/** The stored LLM keys a job on ``repo`` may be run with. */
export function useRunWithKeys(repo?: string | null, enabled = true) {
  return useQuery({
    queryKey: ["run-with", "keys", repo ?? ""],
    queryFn: () => fetchRunWithKeys(repo),
    enabled,
    staleTime: 60 * 1000,
  });
}
