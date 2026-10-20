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

// Canonical hex colors are in graph-utils.ts (AGENT_COLORS). These Tailwind classes map to the same values.
export const ROLE_COLORS: Record<string, string> = {
  explorer: "text-role-explorer",
  developer: "text-role-developer",
  validator: "text-role-validator",
  triage: "text-role-triage",
  reviewer: "text-role-reviewer",
};

export const ROLE_BG_COLORS: Record<string, string> = {
  explorer: "bg-role-explorer",
  developer: "bg-role-developer",
  validator: "bg-role-validator",
  triage: "bg-role-triage",
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

/** The Issues board's columns, in order. */
export const ISSUE_BOARD_COLUMNS: { status: IssueStatus; label: string }[] = [
  { status: "open", label: "Open" },
  { status: "triaging", label: "Triaging" },
  { status: "awaiting_input", label: "Awaiting Input" },
  { status: "in_progress", label: "In Progress" },
  { status: "closed", label: "Closed" },
  { status: "cancelled", label: "Cancelled" },
];
