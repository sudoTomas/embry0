import type { TraceResult } from "./jobs";

/**
 * Trace record as returned by GET /api/v1/traces.
 *
 * Mirrors the columns of the `traces` table (see embry0/storage/migrations/runner.py).
 * The embry0 schema is intentionally narrower than coding-lab's — fields like
 * issue_number, repo, tier, role, validation, etc. are not persisted here.
 */
export interface TraceResponse {
  trace_id: string;
  job_id: string;
  agent_type: string;
  model: string;
  result: TraceResult;
  cost_usd: number;
  duration_ms: number;
  tools_called: Record<string, number>;
  result_summary: string;
  created_at: string;
  /** EMB-35 token columns — 0 on rows that predate migration 38. */
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
}

export interface TraceListResponse {
  traces: TraceResponse[];
  total: number;
  offset: number;
  limit: number;
}

export interface TraceFilters {
  job_id?: string;
  agent_type?: string;
  result?: string;
  limit?: number;
  offset?: number;
}
