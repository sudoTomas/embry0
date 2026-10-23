import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  decideMergeApproval,
  fetchMergeApproval,
  fetchMergeApprovals,
  fetchPendingMergeApprovals,
  type MergeApprovalStatus,
  type MergeDecision,
} from "@/api/mergeApprovals";

/** Pending merge asks; `data === null` when the deployment has the feature off. */
export function usePendingMergeApprovals() {
  return useQuery({
    queryKey: ["merge-approvals", "pending"],
    queryFn: fetchPendingMergeApprovals,
    refetchInterval: 30_000,
    retry: false,
  });
}

/** The asks in one status or all; `data === null` when the feature is off. */
export function useMergeApprovals(status: MergeApprovalStatus | "all") {
  return useQuery({
    queryKey: ["merge-approvals", "list", status],
    queryFn: () => fetchMergeApprovals(status),
    refetchInterval: 30_000,
    retry: false,
  });
}

export function useMergeApproval(approvalId: string | null) {
  return useQuery({
    queryKey: ["merge-approvals", "detail", approvalId],
    queryFn: () => fetchMergeApproval(approvalId!),
    enabled: !!approvalId,
    retry: false,
  });
}

/** Decide one ask; every merge-approval read is stale afterwards, the brief bar's count included. */
export function useDecideMergeApproval() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: MergeDecision }) => decideMergeApproval(id, decision),
    onSettled: () => qc.invalidateQueries({ queryKey: ["merge-approvals"] }),
  });
}
