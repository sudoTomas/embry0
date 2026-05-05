/**
 * Types for the multi-app QA dashboard surface.
 * Mirror athanor/api/schemas/qa_dashboard.py — keep in sync.
 */

export type AppStatus =
  | "passed"
  | "qa_failure"
  | "e2e_failure"
  | "boot_failure"
  | "ready_check_failed"
  | "infra_failure"
  | "skipped"
  | "inconclusive";

export type RunOverallStatus = "passed" | "failed" | "infra_error";

export interface CacheHits {
  prebaked_image: boolean;
  shared_volume: boolean;
  turbo_remote_hits: string[];
  turbo_remote_misses: string[];
}

export interface RepoEntry {
  repo: string;
  latest_run_id: string;
  latest_status: RunOverallStatus;
  latest_started_at: string; // ISO8601
  latest_app_count: number;
}

export interface RunListItem {
  job_id: string;
  repo: string;
  started_at: string;
  overall_status: RunOverallStatus;
  app_count: number;
}

export interface AppResult {
  app_name: string;
  status: AppStatus;
  duration_ms: number;
  cache_hits: CacheHits;
  trace_url: string | null;
  failure_summary: string | null;
}

export interface RunDetail {
  job_id: string;
  repo: string;
  started_at: string;
  overall_status: RunOverallStatus;
  apps: AppResult[];
}

export interface AppHistoryItem {
  job_id: string;
  app_name: string;
  status: AppStatus;
  duration_ms: number;
  started_at: string;
  failure_summary: string | null;
}
