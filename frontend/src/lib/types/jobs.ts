export type JobStatus = "pending" | "running" | "completed" | "failed" | "partial" | "cancelled" | "awaiting_input" | "pr_merged" | "pr_closed" | "paused" | "expired";
export type Tier = "routine" | "standard" | "complex";
export type ProviderMode = "anthropic_api" | "claude_max" | "ollama";
export type TraceResult = "pass" | "fail" | "partial" | "error" | "timeout" | "budget_exceeded";

export interface JobResponse {
  job_id: string;
  status: JobStatus;
  /** Null for non-git jobs (http/local/none contexts). */
  repo: string | null;
  task: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  issue_number: number | null;
  // Internal issues-table FK. NULL means the job did not originate from an
  // embry0 issue (operator/API dispatch) — the Console board's explicit
  // operator-job signal.
  issue_id?: string | null;
  // Latest workflow stage persisted by the executor at each node transition.
  // NULL for legacy rows and jobs that never streamed one. Lets board cards
  // show a stage badge from the poll alone when the WS is down.
  current_stage?: string | null;
  tier: Tier | null;
  provider_mode: ProviderMode | null;
  model: string | null;
  attempts: number;
  pr_url: string | null;
  error_message: string | null;
  error_code?: string | null;
  validation_summary: string | null;
  total_cost_usd: number;
  budget_overrun_usd?: number;
  pipeline_graph?: Record<string, unknown> | null;
  pipeline_source?: string | null;
  pipeline_template?: string | null;
  template_id?: string | null;
  /** Per-(agent, model) rollup from traces (GET /jobs/{id} only). EMB-35 adds token sums. */
  cost_breakdown?: CostBreakdownEntry[];
}

export interface CostBreakdownEntry {
  agent_type: string;
  model: string;
  runs: number;
  cost_usd: number;
  duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
}

export interface JobListResponse {
  jobs: JobResponse[];
  total: number;
  offset: number;
  limit: number;
}

export interface JobCreateRequest {
  repo: string;
  task: string;
  issue_number?: number | null;
  labels?: string[];
  branch?: string | null;
  test_command?: string | null;
  lint_command?: string | null;
  typecheck_command?: string | null;
  max_budget_usd?: number | null;
  max_attempts?: number | null;
  provider_mode?: ProviderMode | null;
  model?: string | null;
  pipeline_graph?: Record<string, unknown> | null;
  environment?: Record<string, string> | null;
}

export interface JobFilters {
  status?: string;
  repo?: string;
  limit?: number;
  offset?: number;
}
