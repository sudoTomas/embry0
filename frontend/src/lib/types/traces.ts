import type { Tier, ProviderMode, TraceResult } from "./jobs";

export interface TraceValidation {
  passed: boolean;
  category: "full_pass" | "partial_pass" | "full_fail";
  summary: string;
}

export interface TraceResponse {
  trace_id: string;
  issue_number: number;
  repo: string;
  timestamp: string;
  attempt: number;
  tier: Tier;
  provider_mode: ProviderMode;
  model: string;
  role: "developer" | "validator" | "explorer";
  result: TraceResult;
  session_id: string | null;
  turns_used: number | null;
  tokens_input: number | null;
  tokens_output: number | null;
  tools_called: Record<string, number>;
  tool_errors: Record<string, number>;
  duration_seconds: number | null;
  cost_usd: number | null;
  error_message: string | null;
  stop_reason: string | null;
  escalated_from: ProviderMode | null;
  escalation_reason: string | null;
  validation: TraceValidation | null;
}

export interface TraceListResponse {
  traces: TraceResponse[];
  total: number;
  offset: number;
  limit: number;
}

export interface TraceFilters {
  trace_id?: string;
  repo?: string;
  role?: string;
  result?: string;
  tier?: string;
  limit?: number;
  offset?: number;
}
