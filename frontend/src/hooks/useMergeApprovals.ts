import { useQuery } from "@tanstack/react-query";
import { fetchPendingMergeApprovals } from "@/api/mergeApprovals";

/** Pending merge asks; `data === null` when the deployment has the feature off. */
export function usePendingMergeApprovals() {
  return useQuery({
    queryKey: ["merge-approvals", "pending"],
    queryFn: fetchPendingMergeApprovals,
    refetchInterval: 30_000,
    retry: false,
  });
}
