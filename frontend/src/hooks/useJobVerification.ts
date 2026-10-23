import { useQuery } from "@tanstack/react-query";
import { fetchJobVerification } from "@/api/jobs";

interface Options {
  /** The job is running and the stream is open. */
  live: boolean;
  /** A `verification_result` event has been seen: the verify node emits it before it writes the row. */
  resultSeen: boolean;
}

/**
 * The job's verification row, null when none. Fetched once at mount; while the
 * job is live it is polled every 15 s only once the event has been seen, so a
 * job without a verify node never produces a steady stream of 404s. The page
 * refetches it on the event's rising edge and on the live-to-terminal transition.
 */
export function useJobVerification(jobId: string | undefined, { live, resultSeen }: Options) {
  return useQuery({
    queryKey: ["jobs", jobId, "verification"],
    queryFn: () => fetchJobVerification(jobId!),
    enabled: !!jobId,
    refetchInterval: live && resultSeen ? 15_000 : false,
  });
}
