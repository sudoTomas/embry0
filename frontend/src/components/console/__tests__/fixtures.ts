import type { JobResponse } from "@/lib/types";
import type { UseLiveJobSummaryResult } from "@/hooks/useLiveJobSummary";

/** Minimal running-job row; override per test. */
export function makeJob(overrides: Partial<JobResponse> = {}): JobResponse {
  return {
    job_id: "abcd1234efgh5678",
    status: "running",
    repo: "acme/widgets",
    task: "Fix the notes editor\nSecond line with detail that is not the title",
    created_at: "2026-07-08T10:00:00Z",
    started_at: "2026-07-08T10:01:00Z",
    finished_at: null,
    issue_number: null,
    issue_id: null,
    current_stage: null,
    tier: null,
    provider_mode: null,
    model: null,
    attempts: 0,
    pr_url: null,
    error_message: null,
    error_code: null,
    validation_summary: null,
    total_cost_usd: 0,
    ...overrides,
  };
}

/** Default (connected, empty) live-summary shape; override per test. */
export function makeSummary(overrides: Partial<UseLiveJobSummaryResult> = {}): UseLiveJobSummaryResult {
  return {
    lastActivity: null,
    latestCost: 0,
    latestTokensIn: 0,
    latestTokensOut: 0,
    currentNode: null,
    attempt: 1,
    isConnected: true,
    isComplete: false,
    ...overrides,
  };
}
