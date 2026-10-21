import { api } from "./client";

/** One merge ask a person answers in the approval chat (GET /merge-approvals). */
export interface MergeApproval {
  id: string;
  repo?: string;
  status: string;
}

interface MergeApprovalList {
  items: MergeApproval[];
  mode?: string;
}

/**
 * The asks still waiting for an answer, or `null` when this deployment has
 * merge approvals off (the route answers 503). The 503 is read as an answer,
 * not an error, so the overview never toasts a server error for a feature the
 * operator did not turn on.
 */
export async function fetchPendingMergeApprovals(): Promise<MergeApproval[] | null> {
  const response = await api.get<MergeApprovalList>("/merge-approvals", {
    params: { status: "pending", limit: 200 },
    validateStatus: (status) => (status >= 200 && status < 300) || status === 503,
  });
  if (response.status === 503) return null;
  return response.data.items;
}
