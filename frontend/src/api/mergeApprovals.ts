import { api } from "./client";

export const MERGE_APPROVAL_STATUSES = [
  "pending",
  "approved",
  "rejected",
  "merged",
  "failed",
  "stale",
  "closed",
  "expired",
] as const;
export type MergeApprovalStatus = (typeof MERGE_APPROVAL_STATUSES)[number];
export type MergeVerdict = "merge" | "review_first" | "dont_merge";
export type ChecksState = "green" | "pending" | "red" | "unknown";
export type MergeDecision = "approve" | "reject";

/** The reviewer agent's verdict as the ask carries it. */
export interface MergeReviewFacts {
  decision: string;
  validation: Record<string, string>;
  critical: number;
  important: number;
  addresses_failure: string;
  duplicate_pr: number | null;
  summary: string;
}

export interface MergeRecommendation {
  verdict: MergeVerdict;
  reasons: string[];
  positives: string[];
  notes: string[];
}

/** The verification gate's summary for the PR's head; `present: false` when the job has no record. */
export interface MergeVerification {
  present: boolean;
  mode?: string | null;
  gate_verdict?: string | null;
  blocking?: boolean;
  human_merge_only?: boolean;
  human_merge_reasons?: string[];
  for_this_head?: boolean;
}

export interface MergeAsk {
  title: string;
  summary: string;
  review: MergeReviewFacts | null;
  head_ref: string;
  base_ref: string;
  additions: number;
  deletions: number;
  changed_files: number;
  denylisted: Record<string, { count: number; paths: string[] }>;
  verification: MergeVerification;
  /** The CI state in a sentence, its class, and the fixed-rule recommendation; absent on an ask written before they were. */
  checks?: string;
  checks_state?: ChecksState;
  recommendation?: MergeRecommendation;
}

/** One merge ask (GET /merge-approvals). */
export interface MergeApproval {
  id: string;
  repo: string;
  pr_number: number;
  pr_url: string;
  head_sha: string;
  job_id: string | null;
  case_id: string | null;
  status: MergeApprovalStatus | string;
  dry_run: boolean;
  decided_by: string | null;
  decided_at: string | null;
  /** The chat the answer came from, or the literal `dashboard`. */
  chat_id: string | null;
  detail: string | null;
  merge_sha: string | null;
  ask: MergeAsk | null;
  posted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MergeApprovalList {
  items: MergeApproval[];
  /** `dry_run` records decisions and merges nothing; `on` merges. */
  mode?: "dry_run" | "on";
}

/** What POST /merge-approvals/{id}/decide answers. */
export interface MergeDecisionResult {
  outcome: string;
  status: string;
  dry_run: boolean;
  pr_url: string;
  head_sha: string;
  /** Why a `refused`, `stale` or `failed` answer left the ask as it is; the row's `detail` carries it too. */
  detail?: string | null;
  /** A sentence for an `already_decided` answer. */
  message?: string;
  /** The ask that replaced a `stale` one. */
  new_approval_id?: string | null;
}

const okOr503 = (status: number) => (status >= 200 && status < 300) || status === 503;

/**
 * The asks, newest first, in one status or all of them; `null` when this
 * deployment has merge approvals off (the route answers 503). The 503 is read
 * as an answer, not an error, so the page shows an empty state and never
 * toasts a server error for a feature the operator did not turn on.
 */
export async function fetchMergeApprovals(status: MergeApprovalStatus | "all"): Promise<MergeApprovalList | null> {
  const response = await api.get<MergeApprovalList>("/merge-approvals", {
    params: status === "all" ? { limit: 200 } : { status, limit: 200 },
    validateStatus: okOr503,
  });
  if (response.status === 503) return null;
  return response.data;
}

/** The asks still waiting for an answer, or `null` when the feature is off (the brief bar's read). */
export async function fetchPendingMergeApprovals(): Promise<MergeApproval[] | null> {
  const list = await fetchMergeApprovals("pending");
  return list === null ? null : list.items;
}

/** One ask by id, or `null` when the feature is off: the route's 503, read as the list reads it. */
export async function fetchMergeApproval(approvalId: string): Promise<MergeApproval | null> {
  const response = await api.get<MergeApproval>(`/merge-approvals/${encodeURIComponent(approvalId)}`, { validateStatus: okOr503 });
  if (response.status === 503) return null;
  return response.data;
}

/**
 * The dashboard's form of the decision route (spec 3.8): the decision word
 * alone. The server names the actor from the session or key and refuses
 * anyone below admin with a 403 the page explains itself.
 */
export async function decideMergeApproval(approvalId: string, decision: MergeDecision): Promise<MergeDecisionResult> {
  const { data } = await api.post<MergeDecisionResult>(
    `/merge-approvals/${encodeURIComponent(approvalId)}/decide`,
    { decision },
    { suppressValidationToast: true },
  );
  return data;
}
