import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { answerOwnerProposal, fetchOwnerProposals, type OwnerProposalAnswer, type OwnerProposalKind } from "@/api/ownerProposals";

const KEY = ["owner-proposals"];

export function useOwnerProposals(filters: { owner?: string; kind?: OwnerProposalKind }) {
  return useQuery({
    queryKey: [...KEY, filters],
    queryFn: () => fetchOwnerProposals(filters),
    refetchInterval: 60_000,
  });
}

export function useAnswerOwnerProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      decision,
      body,
    }: {
      id: string;
      decision: OwnerProposalAnswer["decision"];
      body: { by: string; reason?: string; until?: string };
    }) => answerOwnerProposal(id, decision, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      // An accepted issue or case starts a job; an accepted case leaves the Issues page's proposals.
      qc.invalidateQueries({ queryKey: ["issues"] });
      qc.invalidateQueries({ queryKey: ["cases"] });
    },
  });
}
