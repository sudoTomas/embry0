import { api } from "./client";
import type { CaseAcceptResponse, CaseListResponse } from "@/lib/types";

/**
 * What waits for a person: watcher drafts nobody has accepted or rejected, and
 * organization needs nobody has answered. `awaiting_decision` is the server's
 * rule, not `stage=tracked`: that stage also holds a person's ticket
 * mid-dispatch and a draft whose ticket is already gone from its tracker.
 */
export async function fetchProposals(): Promise<CaseListResponse> {
  const { data } = await api.get<CaseListResponse>("/cases", {
    params: { awaiting_decision: true, limit: 50 },
  });
  return data;
}

/** Dispatch the fix pipeline on a draft. The server refuses (409) a draft whose tracker has its own label path. */
export async function acceptCase(caseId: string): Promise<CaseAcceptResponse> {
  const { data } = await api.post<CaseAcceptResponse>(`/cases/${caseId}/accept`, {});
  return data;
}

/**
 * Close a draft nobody will ship. `cancelled` is what a draft cancelled in its
 * tracker ends as, so both ways of saying no leave the same record. The
 * ticket itself is not touched.
 */
export async function rejectCase(caseId: string): Promise<void> {
  await api.post(`/cases/${caseId}/close`, {
    resolution_path: "cancelled",
    note: "Rejected from the dashboard",
  });
}
