import type { RunWith } from "@/api/runWith";
export type JobStatus = "pending" | "running" | "completed" | "failed" | "partial" | "cancelled" | "awaiting_input" | "pr_merged" | "pr_closed" | "paused" | "expired" | "skipped";
export type TraceResult = "success" | "error" | "running";

/** Every JobStatus, in board-lane order. The `satisfies` + Record guard makes
 * adding a status to the union a build error HERE instead of a silently stale
 * filter dropdown (the pre-RAV-1058 failure mode: JobsPage and ConsolePage
 * each hand-rolled diverging lists). */
export const ALL_JOB_STATUSES = [
  "awaiting_input",
  "paused",
  "pending",
  "running",
  "completed",
  "pr_merged",
  "skipped",
  "failed",
  "partial",
  "cancelled",
  "expired",
  "pr_closed",
] as const satisfies readonly JobStatus[];
// Exhaustiveness: fails to typecheck if ALL_JOB_STATUSES misses a union member.
type _MissingStatuses = Exclude<JobStatus, (typeof ALL_JOB_STATUSES)[number]>;
const _exhaustive: _MissingStatuses extends never ? true : never = true;
void _exhaustive;

/** Mirrors the backend `JobResponse` (embry0/api/schemas/__init__.py) —
 * RAV-1058 staleness sweep dropped the phantom fields the API never returned
 * (tier, provider_mode, model, attempts, validation_summary, pipeline_graph,
 * pipeline_source, template_id). */
export type DeliveryState = "none" | "pr_open" | "merged" | "closed";

export interface JobResponse {
  job_id: string;
  status: JobStatus;
  /** Null for non-git jobs (http/local/none contexts). */
  repo: string | null;
  task: string;
  /** True on list rows fetched with `brief`: `task` is the first line, cut to
   * 200 characters. The full text comes from the job detail endpoint. */
  task_truncated?: boolean;
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
  pipeline_template?: string | null;
  sandbox_profile?: string | null;
  pr_url: string | null;
  /**
   * What became of the job's pull request — independent of `status` (#280).
   * A string, not a union: an unknown value from a newer backend must render,
   * not crash. Optional for back-compat with cached responses.
   */
  delivery_state?: DeliveryState | (string & {});
  /** The forge's time of that state; null until merge sync has confirmed it. */
  delivery_state_at?: string | null;
  error_message: string | null;
  error_code?: string | null;
  total_cost_usd: number;
  budget_overrun_usd?: number;
  pipeline_config?: Record<string, unknown> | null;
  trace_id?: string | null;
  job_kind?: string | null;
  result_summary?: string | null;
  /** The part of a monorepo the job's pull request changes (the repository's `.embry0/areas.yml`). */
  area?: string | null;
  /** Per-(agent, model) rollup from traces (GET /jobs/{id} only). EMB-35 adds token sums. */
  cost_breakdown?: CostBreakdownEntry[];
  /** #281: the job's most recent errored agent runs (GET /jobs/{id} only). */
  agent_failures?: AgentFailure[];
  /** What a paused job waits for: a spent LLM account and when it resets (GET /jobs/{id} only). Null when it is not waiting for one. */
  account_wait?: AccountWait | null;
  /** The job in plain words, its brief's title: on list rows once the brief is written. */
  brief_title?: string;
  /** The job in plain words (GET /jobs/{id} only); null until a model has written it. */
  brief?: JobBrief | null;
  /** The job's status in plain words and what a person can do next (GET /jobs/{id} only). */
  status_words?: JobStatusWords;
}

/** A job in plain words (job briefs), written by a model from its task. */
export interface JobBrief {
  title: string;
  /** What happened or what is asked. */
  what: string;
  /** Why it matters. */
  why: string;
  /** What finished looks like, and what the person gets back. */
  done: string;
  size: "small" | "medium" | "large";
  model: string;
  written_at: string | null;
}

/** A job's status in plain words, worked out by the API from its status and its latest pause. */
export interface JobStatusWords {
  state: string;
  headline: string;
  next: string | null;
  actions: string[];
  /** When a wait ends (UTC ISO); show it in the viewer's time. */
  until: string | null;
}

/** A job parked for an LLM account (spent account waits). `until` is UTC ISO; show it in the viewer's time. */
export interface AccountWait {
  provider: string;
  /** quota_exhausted, rate_limited, local_busy, or the reason the account's quarantine names. */
  reason: string;
  until: string;
  model?: string | null;
  agent?: string | null;
  /** A person chose this model or key for the job, so it does not move to another one. */
  chosen?: boolean;
  mode?: "resume" | "relaunch";
}

/** Why one agent run errored. `stderr_tail` is redacted and capped server-side. */
export interface AgentFailure {
  trace_id: string;
  agent_type: string;
  model?: string | null;
  // Plain string, not a union: the server adds codes faster than the UI ships.
  error_code?: string | null;
  result_summary?: string | null;
  stderr_tail?: string | null;
  created_at?: string | null;
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

/** What a job operates on — mirrors the backend `JobContext` schema
 * (embry0/api/schemas/__init__.py). Per-type field rules are enforced
 * server-side: git needs `repo` (ref optional), http needs an http(s) `url`,
 * local needs an absolute `path`, none takes no fields. */
export type JobContextType = "git" | "http" | "local" | "none";

export interface JobContext {
  type: JobContextType;
  repo?: string | null;
  ref?: string | null;
  url?: string | null;
  path?: string | null;
}

/** Mirrors the backend `JobCreateRequest` (RAV-1058: the old shape carried
 * fields the API never accepted — labels, provider_mode, pipeline_graph…). */
export interface JobCreateRequest {
  repo?: string | null;
  task?: string | null;
  issue_number?: number | null;
  /** pr-review only: the pull request number (GitLab: the merge request iid). */
  number?: number | null;
  pipeline?: "issue-to-pr" | "qa" | "onboard" | "pr-review" | null;
  branch?: string | null;
  context?: JobContext | null;
  pipeline_template?: string | null;
  sandbox_profile?: string | null;
  max_budget_usd?: number | null;
  additional_context?: string | null;
  /** Per-agent model override: agent type → model id. Outranks triage. */
  agent_models?: Record<string, string> | null;
  /** Run with (spec 2026-10-25): a model for every agent and a stored key for its provider. */
  run_with?: RunWith | null;
}

export interface JobFilters {
  status?: string;
  repo?: string;
  limit?: number;
  offset?: number;
}

/** One `job_verifications` row (docs/verification.md, "The result"), from GET /jobs/{id}/verification. */
export interface JobVerification {
  job_id: string;
  repo: string;
  mode: string;
  tier: string;
  gate_verdict: string;
  blocking: boolean;
  browser_qa: string;
  human_merge_only: boolean;
  base_sha: string;
  head_sha: string;
  /** The full VerificationResult document; `gate.targets` feeds the gate table. */
  report: {
    tier?: { tier?: string; reason?: string };
    gate?: {
      verdict?: string;
      blocking?: boolean;
      base_ref?: string;
      targets?: { target: string; status: string; comparison?: string; new_failures?: string[]; preexisting?: string[]; flaky?: string[]; fixed?: string[]; blocking?: boolean }[];
    };
    browser_qa?: { planned?: string; reason_code?: string; verdict?: string };
    human_merge_only?: boolean;
    human_merge_reasons?: string[];
    config_error?: string | null;
  } & Record<string, unknown>;
  published_at: string | null;
  created_at: string;
  updated_at: string;
}

/** One agent's stored conversation for a job, redacted server-side (GET /jobs/{id}/agents/{type}/session). */
export interface AgentSession {
  job_id: string;
  agent_type: string;
  mode: string | null;
  session_id: string | null;
  last_turn_at: string | null;
  /** Anthropic Messages shape: role plus a string or a list of content blocks. */
  messages: { role: string; content: string | { type: string; text?: string; name?: string; input?: unknown; content?: unknown; tool_use_id?: string }[] }[];
}
