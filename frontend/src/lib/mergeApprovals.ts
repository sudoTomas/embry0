import type { BadgeTone } from "@/components/ui/Badge";
import type { ChecksState, MergeApproval, MergeVerdict } from "@/api/mergeApprovals";

export const VERDICT_LABELS: Readonly<Record<MergeVerdict, string>> = {
  merge: "Merge",
  review_first: "Review first",
  dont_merge: "Don't merge",
};

export const VERDICT_TONES: Readonly<Record<MergeVerdict, BadgeTone>> = {
  merge: "success",
  review_first: "warning",
  dont_merge: "error",
};

export const CHECKS_TONES: Readonly<Record<ChecksState, BadgeTone>> = {
  green: "success",
  pending: "info",
  red: "error",
  unknown: "neutral",
};

/** How an ask is named everywhere: the repository and the PR number, as GitHub writes them. */
export function approvalTitle(row: Pick<MergeApproval, "repo" | "pr_number">): string {
  return `${row.repo}#${row.pr_number}`;
}

/** Where the decision came from, for a person: the dashboard, or the chat. */
export function decidedVia(chatId: string | null): string {
  return chatId === "dashboard" ? "from the dashboard" : "in the approval chat";
}
