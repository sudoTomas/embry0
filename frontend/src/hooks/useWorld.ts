import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { cancelJob } from "@/api/jobs";
import type { RunWith } from "@/api/runWith";
import {
  acceptLead,
  approveMerge,
  evaluateQuest,
  fetchCharacter,
  fetchQuest,
  fetchWorld,
  rejectQuest,
  sendQuestBack,
  writeQuest,
  type Rating,
} from "@/api/world";

/** How often the guild hall reads the world: parties move between reads. */
export const WORLD_POLL_MS = 5_000;

const WORLD_KEY = ["world"] as const;

export function useWorld() {
  return useQuery({ queryKey: WORLD_KEY, queryFn: fetchWorld, refetchInterval: WORLD_POLL_MS, staleTime: 2_000 });
}

export function useCharacter(agentType: string | null) {
  return useQuery({
    queryKey: ["world", "character", agentType],
    queryFn: () => fetchCharacter(agentType as string),
    enabled: !!agentType,
    refetchInterval: 15_000,
  });
}

export function useQuest(jobId: string | null) {
  return useQuery({
    queryKey: ["world", "quest", jobId],
    queryFn: () => fetchQuest(jobId as string),
    enabled: !!jobId,
  });
}

/** Every mutation here changes what the hall shows: read the world (and its quests and characters) again. */
function useWorldMutation<TVars, TResult>(fn: (vars: TVars) => Promise<TResult>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => void qc.invalidateQueries({ queryKey: WORLD_KEY }) });
}

export function useSendParty() {
  return useWorldMutation((v: { leadId: string; by: string; runWith?: RunWith }) => acceptLead(v.leadId, v.by, v.runWith));
}

export function useWriteQuest() {
  return useWorldMutation(writeQuest);
}

export function useRateQuest() {
  return useWorldMutation((v: { jobId: string; rating?: Rating; verdict?: "merged"; note?: string }) =>
    evaluateQuest(v.jobId, { rating: v.rating, verdict: v.verdict, note: v.note }),
  );
}

export function useSendBack() {
  return useWorldMutation((v: { jobId: string; notes: string; runWith?: RunWith }) => sendQuestBack(v.jobId, v.notes, v.runWith));
}

export function useRejectQuest() {
  return useWorldMutation((v: { jobId: string; note: string; closePr: boolean }) => rejectQuest(v.jobId, v.note, v.closePr));
}

export function useApproveMerge() {
  return useWorldMutation((approvalId: string) => approveMerge(approvalId));
}

export function useRecall() {
  return useWorldMutation((jobId: string) => cancelJob(jobId));
}
