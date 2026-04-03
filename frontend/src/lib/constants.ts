export const JOB_STATUS_COLORS: Record<string, string> = {
  pending: "text-warning",
  running: "text-primary",
  completed: "text-success",
  failed: "text-destructive",
  cancelled: "text-muted-foreground",
  awaiting_input: "text-amber-400",
};

export const JOB_STATUS_BG_COLORS: Record<string, string> = {
  pending: "bg-warning/10",
  running: "bg-primary/10",
  completed: "bg-success/10",
  failed: "bg-destructive/10",
  cancelled: "bg-muted/10",
  awaiting_input: "bg-amber-400/10",
};

export const TIER_COLORS: Record<string, string> = {
  routine: "text-success",
  standard: "text-warning",
  complex: "text-destructive",
};

export const RESULT_COLORS: Record<string, string> = {
  pass: "text-success",
  fail: "text-destructive",
  partial: "text-warning",
  error: "text-destructive",
  timeout: "text-muted-foreground",
  budget_exceeded: "text-warning",
};

export const ISSUE_STATE_COLORS: Record<string, string> = {
  open: "text-success",
  closed: "text-muted-foreground",
};

// Canonical hex colors are in graph-utils.ts (AGENT_COLORS). These Tailwind classes map to the same values.
export const ROLE_COLORS: Record<string, string> = {
  explorer: "text-role-explorer",
  developer: "text-role-developer",
  validator: "text-role-validator",
  triage: "text-role-triage",
};

export const ROLE_BG_COLORS: Record<string, string> = {
  explorer: "bg-role-explorer",
  developer: "bg-role-developer",
  validator: "bg-role-validator",
  triage: "bg-role-triage",
};

export const ROLE_BORDER_COLORS: Record<string, string> = {
  explorer: "border-l-role-explorer",
  developer: "border-l-role-developer",
  validator: "border-l-role-validator",
  triage: "border-l-role-triage",
};

export const ROLE_LABELS: Record<string, string> = {
  explorer: "Explorer",
  developer: "Developer",
  validator: "Validator",
  triage: "Triage",
};

