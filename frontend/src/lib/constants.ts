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

// ===== Pipeline Phase Metadata =====

export interface PhaseConfig {
  name: string;
  label: string;
  description: string;
  color: string;
  icon: string;
}

export const PIPELINE_PHASES: Record<string, PhaseConfig> = {
  triage: { name: "triage", label: "TRIAGE", description: "Analyze issue and configure pipeline", color: "#10b981", icon: "target" },
  developer: { name: "developer", label: "DEVELOP", description: "Implement code changes, commit, push, create PR", color: "#f59e0b", icon: "code-2" },
  validator: { name: "validator", label: "VALIDATE", description: "Run tests, lint, type checks", color: "#22c55e", icon: "shield-check" },
  reviewer: { name: "reviewer", label: "REVIEW", description: "Code review and approval", color: "#ec4899", icon: "scan-eye" },
  output: { name: "output", label: "OUTPUT", description: "Final results", color: "#f43f5e", icon: "send" },
};

export const AGENT_STATUS_COLORS: Record<string, string> = {
  pending: "rgba(255,255,255,0.15)",
  running: "#f59e0b",
  completed: "#10b981",
  failed: "#ef4444",
};

export function getPhaseForAgent(agentType: string): PhaseConfig {
  return PIPELINE_PHASES[agentType] ?? PIPELINE_PHASES.output;
}

