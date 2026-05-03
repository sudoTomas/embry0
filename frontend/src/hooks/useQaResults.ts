import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import {
  listQaAttempts,
  getQaResult,
  type QAAttemptListEntry,
  type QAResult,
} from "@/api/qa-artifacts";

export function useQaAttempts(
  jobId: string,
  jobIsLive: boolean,
): UseQueryResult<QAAttemptListEntry[]> {
  return useQuery<QAAttemptListEntry[]>({
    queryKey: ["qa-attempts", jobId],
    queryFn: () => listQaAttempts(jobId),
    refetchInterval: jobIsLive ? 5000 : false,
  });
}

export function useQaResult(
  jobId: string,
  attemptN: number | null,
  hasResult: boolean,
): UseQueryResult<QAResult> {
  return useQuery<QAResult>({
    queryKey: ["qa-result", jobId, attemptN],
    queryFn: () => getQaResult(jobId, attemptN!),
    enabled: attemptN != null && hasResult,
  });
}
