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
  /** Extended 2026-05-03 — companion-style dashboard density. Optional for back-compat. */
  top_expensive_issues?: ExpensiveIssue[];
  cost_by_repo?: RepoCost[];
}
