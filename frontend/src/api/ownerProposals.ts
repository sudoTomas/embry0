import { api } from "./client";
import type { RunWith } from "./runWith";

/** Work a repository's owners could start (owner proposals, spec 2026-10-24); the last five are leads embry0 found. */
export type OwnerProposalKind =
  | "case"
  | "issue"
  | "finding"
  | "ticket"
  | "ci"
  | "alert"
  | "flaky"
  | "review"
  | "crack"
  | "weed";

/** The proposal in plain words, written by a model (proposal briefs, spec 2026-10-25). */
export interface OwnerProposalBrief {
  title: string;
  /** What is wrong or missing, and who or what it affects. */
  problem: string;
  /** What embry0 would do, and what the owner gets back. */
  fix: string;
  size: "small" | "medium" | "large";
  model: string;
  written_at: string | null;
}

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
  /** Null until the brief writer has written it (or when it was written from an older version of the proposal). */
  brief?: OwnerProposalBrief | null;
  /** A backlog ticket below the readiness floor: shown here, not asked in chat. */
  held?: boolean;
}

export interface OwnerProposalAnswer {
  proposal_id: string;
  decision: "accept" | "decline" | "snooze";
  by: string;
  job_id: string | null;
  until: string | null;
}

export async function fetchOwnerProposals(params: {
  owner?: string;
  kind?: OwnerProposalKind;
  include_held?: boolean;
}): Promise<OwnerProposal[]> {
  const { data } = await api.get<{ proposals: OwnerProposal[] }>("/proposals", { params });
  return data.proposals;
}

export async function answerOwnerProposal(
  id: string,
  decision: OwnerProposalAnswer["decision"],
  body: { by: string; reason?: string; until?: string; run_with?: RunWith },
): Promise<OwnerProposalAnswer> {
  const { data } = await api.post<OwnerProposalAnswer>(`/proposals/${encodeURIComponent(id)}/${decision}`, body);
  return data;
}
