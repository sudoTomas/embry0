import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { acceptCase, fetchProposals, rejectCase } from "@/api/cases";
import type { RunWith } from "@/api/runWith";

const PROPOSALS_KEY = ["cases", "awaiting-decision"];

export function useProposals() {
  return useQuery({
    queryKey: PROPOSALS_KEY,
    queryFn: fetchProposals,
    refetchInterval: 30_000,
  });
}

export function useAcceptProposal() {
  const qc = useQueryClient();
  return useMutation({
    // A bare id is the usual run; with runWith, the model and key the job runs with.
    mutationFn: (vars: string | { caseId: string; runWith?: RunWith }) =>
      typeof vars === "string" ? acceptCase(vars) : acceptCase(vars.caseId, vars.runWith),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: PROPOSALS_KEY });
      // Accepting creates the issue row the list below shows.
      qc.invalidateQueries({ queryKey: ["issues"] });
    },
  });
}

export function useRejectProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (caseId: string) => rejectCase(caseId),
    onSuccess: () => qc.invalidateQueries({ queryKey: PROPOSALS_KEY }),
  });
}
