import { useQuery } from "@tanstack/react-query";
import { getActiveSandboxes } from "@/api/sandboxes-active";

export function useActiveSandboxes(includeStopped = false) {
  return useQuery({
    queryKey: ["sandboxes-active", includeStopped],
    queryFn: () => getActiveSandboxes(includeStopped),
    refetchInterval: 5000, // observability — poll every 5s
  });
}
