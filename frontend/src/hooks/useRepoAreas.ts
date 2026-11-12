import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  applyAreasProposal,
  fetchAreasProposal,
  fetchPendingAreasProposals,
  fetchRepoAreas,
  previewRepoAreas,
  rejectAreasProposal,
  saveRepoAreas,
  startMapAreas,
} from "@/api/areas";
import type { RunWith } from "@/api/runWith";

const areasKey = (repo: string) => ["repos", "areas", repo] as const;
const proposalKey = (repo: string) => ["repo-areas-proposal", repo] as const;
export const PENDING_AREAS_KEY = ["areas-proposals", "pending"] as const;

/** Poll while a map runs; every other state changes on a person's click. */
const POLL_MS = 15_000;

/** Everything a write, a map or a decision on one can change: the areas, the map, the history, the brief. */
function invalidate(qc: QueryClient, repo: string) {
  for (const queryKey of [areasKey(repo), proposalKey(repo), ["repo-history", repo], PENDING_AREAS_KEY]) {
    void qc.invalidateQueries({ queryKey });
  }
}

/** A repository's areas and its newest map; polls while a map runs. */
export function useRepoAreas(repo: string) {
  return useQuery({
    queryKey: areasKey(repo),
    queryFn: () => fetchRepoAreas(repo),
    refetchInterval: (query) =>
      query.state.data?.latest_job?.live || query.state.data?.proposal?.status === "running" ? POLL_MS : false,
  });
}

export function useAreasProposal(repo: string, enabled = true) {
  return useQuery({
    queryKey: proposalKey(repo),
    queryFn: () => fetchAreasProposal(repo),
    enabled,
    refetchInterval: (query) => (query.state.data?.status === "running" ? POLL_MS : false),
  });
}

export function useMapAreas(repo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runWith?: RunWith) => startMapAreas(repo, runWith),
    onSuccess: () => invalidate(qc, repo),
  });
}

/** Read the areas in again, as stored now: after a conflict, the version an edit is saved over. */
export function useRereadRepoAreas(repo: string) {
  const qc = useQueryClient();
  return () => qc.fetchQuery({ queryKey: areasKey(repo), queryFn: () => fetchRepoAreas(repo), staleTime: 0 });
}

export function usePreviewRepoAreas(repo: string) {
  return useMutation({
    mutationFn: ({ yamlText, generation }: { yamlText: string; generation: number }) =>
      previewRepoAreas(repo, yamlText, generation),
  });
}

export function useSaveRepoAreas(repo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ yamlText, generation }: { yamlText: string; generation: number }) =>
      saveRepoAreas(repo, yamlText, generation),
    onSuccess: () => invalidate(qc, repo),
  });
}

export function useDecideAreasProposal(repo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, decision }: { id: string; decision: "apply" | "reject" }) => {
      if (decision === "apply") return applyAreasProposal(repo, id);
      await rejectAreasProposal(repo, id);
      return { revision_id: null };
    },
    // A refused apply (the map went stale) changes the map too.
    onSettled: () => invalidate(qc, repo),
  });
}

/** Every map waiting for a person, for the Overview's brief. */
export function usePendingAreasProposals() {
  return useQuery({ queryKey: PENDING_AREAS_KEY, queryFn: fetchPendingAreasProposals, refetchInterval: 30_000 });
}
