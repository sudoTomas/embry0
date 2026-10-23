import type { IssueStatus } from "@/lib/types";

export const ISSUE_STATUS_COLORS: Record<string, string> = {
  open: "text-success",
  triaging: "text-warning",
  awaiting_input: "text-warning",
  in_progress: "text-info",
  closed: "text-purple",
  cancelled: "text-muted-foreground",
};

export const ISSUE_STATUS_ICONS: Record<string, string> = {
  open: "●",
  triaging: "◉",
  awaiting_input: "❓",
  in_progress: "●",
  closed: "✓",
  cancelled: "✕",
};

export const ISSUE_PRIORITY_COLORS: Record<string, string> = {
  critical: "text-destructive",
  high: "text-warning",
  medium: "text-fg-secondary",
  low: "text-muted-foreground",
};

export const ISSUE_PRIORITY_BG_COLORS: Record<string, string> = {
  critical: "bg-red-dim text-destructive",
  high: "bg-amber-dim text-warning",
  medium: "bg-white/[0.06] text-fg-secondary",
  low: "bg-white/[0.04] text-fg-muted",
};

export const RESULT_COLORS: Record<string, string> = {
  success: "text-success",
  error: "text-destructive",
  running: "text-warning",
};

export const ISSUE_STATE_COLORS: Record<string, string> = {
  open: "text-success",
  closed: "text-muted-foreground",
};

/** The Issues board's columns, in order. */
export const ISSUE_BOARD_COLUMNS: { status: IssueStatus; label: string }[] = [
  { status: "open", label: "Open" },
  { status: "triaging", label: "Triaging" },
  { status: "awaiting_input", label: "Awaiting Input" },
  { status: "in_progress", label: "In Progress" },
  { status: "closed", label: "Closed" },
  { status: "cancelled", label: "Cancelled" },
];
