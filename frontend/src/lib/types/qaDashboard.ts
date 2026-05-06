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

export interface BootPhaseDetail {
  outcome: "passed" | "timeout" | "startup_failed";
  attempts: number;
  duration_ms: number;
  failed_checks: string[];
  boot_stdout_tail: string;
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
  boot_phase?: BootPhaseDetail | null;
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

/**
 * Phase 5D: a single workspace dependency edge.
 *
 * Field names match the backend (`source`/`target` rather than `from`/`to`)
 * to sidestep `from`-as-reserved-keyword issues in both Python and JS.
 * Currently always empty — extracting workspace edges from the
 * npm-workspaces-turbo provider is a follow-up.
 */
export interface DepEdge {
  source: string;
  target: string;
}

/**
 * Phase 5D: GET /api/v1/qa/runs/{run_id}/affected_set payload.
 *
 * Mirrors the qa_run_metadata row qa_orchestrator_node persisted at fan-out
 * time. `apps_skipped` is every declared app NOT in apps_to_qa; the diff
 * (`changed_files` + `base_branch`) shows what drove selection;
 * `force_all_apps` flags qa_required=always / explicit override paths.
 */
export interface AffectedSetResponse {
  job_id: string;
  apps_to_qa: string[];
  apps_skipped: string[];
  force_all_apps: boolean;
  changed_files: string[];
  base_branch: string;
  dep_graph: DepEdge[];
}

/**
 * Phase 5E: per-layer cache hit/miss aggregate over a window.
 *
 * Mirrors `athanor/api/schemas/qa_dashboard.py:CacheLayerStats`. The
 * `layer` literal is repeated server-side so a contract drift surfaces
 * as a TypeScript error, not silent data loss.
 */
export interface CacheLayerStats {
  layer: "prebaked_image" | "shared_volume" | "turbo_remote";
  hits: number;
  misses: number;
  hit_ratio: number;
}

/**
 * Phase 5E: GET /api/v1/qa/repos/{repo}/cache/analytics payload.
 *
 * `layers` always holds exactly three entries (one per layer);
 * `cold_cache_apps` is the alphabetised list of apps whose aggregate
 * hit_ratio fell below 0.25 over >= 3 sub-tasks in the window.
 */
export interface CacheAnalyticsResponse {
  repo: string;
  window_days: number;
  total_runs: number;
  total_subtasks: number;
  layers: CacheLayerStats[];
  cold_cache_apps: string[];
}

/**
 * Phase 5G: per-repo workspace_provider override returned from
 * GET /api/v1/qa/admin/providers and POST /api/v1/qa/admin/providers/{repo}.
 *
 * Mirrors `athanor.api.schemas.qa_dashboard.WorkspaceProviderOverride`.
 * When a row exists for a repo, the orchestrator uses it instead of the
 * .athanor/qa.yaml workspace_provider section.
 */
export interface WorkspaceProviderOverride {
  repo: string;
  provider_type: string;
  config: Record<string, unknown>;
  updated_at: string; // ISO timestamp
}

/**
 * Phase 5G: request body for POST /api/v1/qa/admin/providers/{repo}.
 *
 * The form edits an existing provider's (type, config). Adding new
 * provider types is out of scope; the orchestrator's load_provider()
 * does the type validation when the next QA run picks up the override.
 */
export interface WorkspaceProviderOverrideUpsert {
  provider_type: string;
  config: Record<string, unknown>;
}

/**
 * Phase 5F: one day in a flake-heatmap row's daily grid.
 *
 * The grid spans the full window so even days with zero flakes appear,
 * keeping the dashboard heatmap CSS grid stable across apps.
 */
export interface FlakeDailyEntry {
  date: string; // 'YYYY-MM-DD' (UTC calendar day)
  flakes: number;
}

/**
 * Phase 5F: one app's row in the flake heatmap.
 *
 * `flake_score` is normalised hits/runs in [0, 1]; the dashboard sorts
 * rows by this value desc so worst offenders surface first.
 */
export interface FlakeRow {
  app_name: string;
  total_runs: number;
  flake_count: number;
  flake_score: number;
  daily: FlakeDailyEntry[];
}

/**
 * Phase 5F: GET /api/v1/qa/repos/{repo}/flake payload.
 *
 * Backend caps `window_days` at 90 — keep the dashboard picker in
 * lockstep so users can't exceed it from the URL bar.
 */
export interface FlakeResponse {
  repo: string;
  window_days: number;
  apps: FlakeRow[];
}
