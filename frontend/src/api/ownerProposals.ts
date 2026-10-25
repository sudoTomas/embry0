import { api } from "./client";

/** Work a repository's owners could start (owner proposals, spec 2026-10-24). */
export type OwnerProposalKind = "case" | "issue" | "finding";

export interface OwnerProposal {
  /** `case:<id>`, `issue:<id>` or `finding:<hash>`. */
  id: string;
  kind: OwnerProposalKind;
  repo: string;
  /** The part of a monorepo it is about, from the repository's `.embry0/areas.yml`; null when it names none. */
  area?: string | null;
  /** GitHub logins: the area's owners, else the repository's owners preference, else its CODEOWNERS. */
  owners: string[];
  title: string;
  /** A sentence a chat message can carry. */
  summary: string;
  details: string;
  /** What accepting does, in words. */
  action: string;
  url: string | null;
  created_at: string;
  /** A finding's file and line. */
  path?: string;
  line?: number | null;
}

export interface OwnerProposalAnswer {
  proposal_id: string;
  decision: "accept" | "decline" | "snooze";
  by: string;
  job_id: string | null;
  until: string | null;
}

export async function fetchOwnerProposals(params: { owner?: string; kind?: OwnerProposalKind }): Promise<OwnerProposal[]> {
  const { data } = await api.get<{ proposals: OwnerProposal[] }>("/proposals", { params });
  return data.proposals;
}

export async function answerOwnerProposal(
  id: string,
  decision: OwnerProposalAnswer["decision"],
  body: { by: string; reason?: string; until?: string },
): Promise<OwnerProposalAnswer> {
  const { data } = await api.post<OwnerProposalAnswer>(`/proposals/${encodeURIComponent(id)}/${decision}`, body);
  return data;
}
