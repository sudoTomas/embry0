import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  applyProposal,
  fetchProposal,
  fetchSetupCost,
  rejectProposal,
  startSetup,
  type QaCheckWarningCode,
  type SetupProposal,
} from "@/api/repoSetup";

/** The cadence useProposals polls at. */
const POLL_MS = 30_000;

/** Poll only while embry0 is still writing the proposal; every other state changes on a person's click. */
export function proposalRefetchInterval(data: SetupProposal | null | undefined): number | false {
  return data?.status === "running" ? POLL_MS : false;
}

/** Everything on the repo page a setup run or a decision on its proposal can change. */
function invalidateAfterSetup(qc: QueryClient, fullRepo: string) {
  for (const queryKey of [
    ["repo-readiness", fullRepo],
    ["repo-history", fullRepo],
    ["repo-setup-proposal", fullRepo],
  ]) {
    void qc.invalidateQueries({ queryKey });
  }
}

export function useSetupCost(fullRepo: string) {
  return useQuery({
    queryKey: ["repo-setup-cost", fullRepo],
    queryFn: () => fetchSetupCost(fullRepo),
    enabled: !!fullRepo,
  });
}

export function useStartSetup(fullRepo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => startSetup(fullRepo),
    onSuccess: () => invalidateAfterSetup(qc, fullRepo),
  });
}

export function useLatestProposal(fullRepo: string) {
  return useQuery({
    queryKey: ["repo-setup-proposal", fullRepo],
    queryFn: () => fetchProposal(fullRepo),
    enabled: !!fullRepo,
    refetchInterval: (query) => proposalRefetchInterval(query.state.data),
  });
}

export function useApplyProposal(fullRepo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, acknowledged }: { id: string; acknowledged: QaCheckWarningCode[] }) =>
      applyProposal(fullRepo, id, acknowledged),
    // A refusal refreshes too: a 409 left the proposal stale, a 422 may name
    // a warning the card has not shown yet.
    onSettled: () => invalidateAfterSetup(qc, fullRepo),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["qa-config", fullRepo] });
      void qc.invalidateQueries({ queryKey: ["repos", "detail", fullRepo] });
    },
  });
}

export function useRejectProposal(fullRepo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => rejectProposal(fullRepo, id),
    onSuccess: () => invalidateAfterSetup(qc, fullRepo),
  });
}
