import type { Tier } from "./jobs";

export interface RecentIssue {
  trace_id: string;
  issue_number: number;
  repo: string;
  tier: Tier;
  timestamp: string;
  passed: boolean;
  cost_usd?: number;
  status?: string;
}

export interface ExpensiveIssue {
  trace_id: string;
  issue_number: number;
  title: string;
  repo: string;
  cost_usd: number;
  job_count: number;
}

export interface RepoCost {
  repo: string;
  cost_usd: number;
  job_count: number;
}

/** EMB-35: per-agent-phase token rollup from traces. */
export interface AgentTokenStats {
  agent_type: string;
  runs: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  cost_usd: number;
  cache_hit_rate: number;
}

/** EMB-35: lifetime token totals across all traces. */
export interface TokenTotals {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  cache_hit_rate: number;
}

export interface StatsResponse {
  total_issues: number;
  total_jobs: number;
  completed: number;
  failed: number;
  /** Live state (extended 2026-05-03). Optional for back-compat. */
  running?: number;
  queued?: number;
  awaiting_input?: number;
  paused?: number;
  success_rate: number;
  total_cost_usd: number;
  cost_by_tier: Record<string, number>;
  failure_categories: Record<string, number>;
  success_rate_by_tier: Record<string, number>;
  avg_attempts_by_tier: Record<string, number>;
  avg_cost_per_tier: Record<string, number>;
  daily_cost_usd: number;
  monthly_cost_usd: number;
  queue_depth: number;
  recent_issues: RecentIssue[];
  /** Extended 2026-05-03 — Companion-style dashboard density. Optional for back-compat. */
  top_expensive_issues?: ExpensiveIssue[];
  cost_by_repo?: RepoCost[];
  /** EMB-35 — token observability. Optional for back-compat. */
  tokens_by_agent?: AgentTokenStats[];
  token_totals?: TokenTotals;
}
