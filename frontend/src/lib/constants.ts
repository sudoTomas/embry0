export const ISSUE_STATUS_COLORS: Record<string, string> = {
  open: "text-success",
  triaging: "text-warning",
  awaiting_input: "text-warning",
  in_progress: "text-blue-400",
  closed: "text-purple-400",
  cancelled: "text-muted-foreground",
};

export const ISSUE_STATUS_BG_COLORS: Record<string, string> = {
  open: "bg-success/10",
  triaging: "bg-warning/10",
  awaiting_input: "bg-warning/10",
  in_progress: "bg-blue-400/10",
  closed: "bg-purple-400/10",
  cancelled: "bg-muted-foreground/10",
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
  critical: "text-red-400",
  high: "text-warning",
  medium: "text-blue-400",
  low: "text-muted-foreground",
};

export const ISSUE_PRIORITY_BG_COLORS: Record<string, string> = {
  critical: "bg-red-500/20 text-red-300",
  high: "bg-warning/20 text-amber-300",
  medium: "bg-blue-500/20 text-blue-300",
  low: "bg-zinc-500/20 text-zinc-400",
};

export const JOB_STATUS_COLORS: Record<string, string> = {
  pending: "text-warning",
  running: "text-primary",
  completed: "text-success",
  failed: "text-destructive",
  partial: "text-warning",
  cancelled: "text-muted-foreground",
  awaiting_input: "text-amber-400",
  pr_merged: "text-purple-400",
  pr_closed: "text-muted-foreground",
  paused: "text-amber-400",
  expired: "text-zinc-500",
  skipped: "text-muted-foreground",
};

export const JOB_STATUS_BG_COLORS: Record<string, string> = {
  pending: "bg-warning/10",
  running: "bg-primary/10",
  completed: "bg-success/10",
  failed: "bg-destructive/10",
  partial: "bg-warning/10",
  cancelled: "bg-muted/10",
  awaiting_input: "bg-amber-400/10",
  pr_merged: "bg-purple-400/10",
  pr_closed: "bg-muted/10",
  paused: "bg-amber-400/10",
  expired: "bg-zinc-500/10",
  skipped: "bg-muted/10",
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
