import { useQuery } from "@tanstack/react-query";
import { fetchAgentSession } from "@/api/jobs";

/** One agent's stored transcript, fetched only while the drawer is open. */
export function useAgentSession(jobId: string | undefined, agentType: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["jobs", jobId, "session", agentType],
    queryFn: () => fetchAgentSession(jobId!, agentType!),
    enabled: enabled && !!jobId && !!agentType,
    staleTime: 60_000,
  });
}
