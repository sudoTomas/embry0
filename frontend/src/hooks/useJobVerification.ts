import { useQuery } from "@tanstack/react-query";
import { fetchJobVerification } from "@/api/jobs";

/** The job's verification row, null when none; refetched while the job is live so the tab appears when the verify node finishes. */
export function useJobVerification(jobId: string | undefined, live: boolean) {
  return useQuery({
    queryKey: ["jobs", jobId, "verification"],
    queryFn: () => fetchJobVerification(jobId!),
    enabled: !!jobId,
    refetchInterval: live ? 15_000 : false,
  });
}
