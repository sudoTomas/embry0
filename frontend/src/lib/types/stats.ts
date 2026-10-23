export interface RecentIssue {
  /** The issue's id: the row links to /issues/:trace_id. */
  trace_id: string;
  /** GitHub issue number; 0 for every other tracker. */
  issue_number: number;
  repo: string;
  /** The rest name the issue; absent from a backend older than them. */
  title?: string;
  tracker?: string | null;
  tracker_key?: string | null;
  /** The issue's own status, apart from its latest job's. */
  issue_status?: string;
  job_status?: string | null;
  timestamp: string;
  passed: boolean;
  cost_usd?: number;
  /** The latest job's status, else the issue's (blended; kept for older backends). */
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

/**
 * What became of the pull requests jobs produced, counted per job and
 * independent of job status (#280): a `failed` job's PR can still merge.
 */
export interface DeliveryStats {
  /** Job counts, by what became of the PR the job produced. */
  none: number;
  pr_open: number;
  merged: number;
  closed: number;
  /**
   * Jobs whose PR merged although their status is not a success. Includes
   * work a PERSON finished from an embry0 branch — delivered, but not
   * autonomous throughput.
   */
  merged_despite_job_status: number;
  /**
   * Distinct pull requests — a different population from jobs, since a retry
   * and a follow-up share one PR. Optional for back-compat.
   */
  prs_merged?: number;
  prs_closed?: number;
  prs_open?: number;
  /** prs_merged / (prs_merged + prs_closed). */
  merge_rate: number;
}

export interface QaPrebuiltImageRef {
  repo: string;
  image_tag: string;
}

/** Which repositories' pre-built QA images sit on the current base (#334); null when the stack has no docker handle. */
export interface QaPrebuiltImagesSummary {
  active: number;
  fresh: number;
  stale: QaPrebuiltImageRef[];
  unknown: QaPrebuiltImageRef[];
}

/** The credential registry's size and health; null when the registry is not wired. */
export interface CredentialsSummary {
  count: number;
  quarantined: number;
  disabled: number;
  env_fallback_keys: string[];
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
  /** Share of jobs that completed OR whose PR merged (#280), over jobs that were not skipped (#474). */
  success_rate: number;
  /** Share of jobs whose own status is a success, whatever became of the PR. Optional for back-compat. */
  job_success_rate?: number;
  job_succeeded?: number;
  cancelled?: number;
  /** Jobs that ended without work because an open PR already covered the ticket (#474). */
  skipped?: number;
  delivery?: DeliveryStats;
  total_cost_usd: number;
  failure_categories: Record<string, number>;
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
  qa_prebuilt_images?: QaPrebuiltImagesSummary | null;
  credentials?: CredentialsSummary | null;
}
