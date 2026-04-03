import type { Tier } from "./jobs";

export interface RecentIssue {
  trace_id: string;
  issue_number: number;
  repo: string;
  tier: Tier;
  timestamp: string;
  passed: boolean;
}

export interface StatsResponse {
  total_issues: number;
  total_cost_usd: number;
  success_rate: number;
  cost_by_tier: Record<string, number>;
  failure_categories: Record<string, number>;
  success_rate_by_tier: Record<string, number>;
  avg_attempts_by_tier: Record<string, number>;
  avg_cost_per_tier: Record<string, number>;
  daily_cost_usd: number;
  monthly_cost_usd: number;
  queue_depth: number;
  recent_issues: RecentIssue[];
}
