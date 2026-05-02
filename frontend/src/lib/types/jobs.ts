export type JobStatus = "pending" | "running" | "completed" | "failed" | "cancelled" | "awaiting_input" | "pr_merged" | "pr_closed" | "paused" | "expired";
export type Tier = "routine" | "standard" | "complex";
export type ProviderMode = "anthropic_api" | "claude_max" | "ollama";
export type TraceResult = "pass" | "fail" | "partial" | "error" | "timeout" | "budget_exceeded";

export interface JobResponse {
  job_id: string;
  status: JobStatus;
  repo: string;
  task: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  issue_number: number | null;
  tier: Tier | null;
  provider_mode: ProviderMode | null;
  model: string | null;
  attempts: number;
  pr_url: string | null;
  error_message: string | null;
  validation_summary: string | null;
  total_cost_usd: number;
  pipeline_graph?: Record<string, unknown> | null;
  pipeline_source?: string | null;
  pipeline_template?: string | null;
  template_id?: string | null;
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
