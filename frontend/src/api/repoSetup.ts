import { api } from "./client";

/** Where a repository stands overall (GET /repos/{repo}/readiness). */
export type ReadinessStatus = "ready" | "steps_left" | "needs_admin" | "setup_to_review";

/** The required steps, in order; `work_trigger` is the one optional row. */
export type ReadinessStep = "code_access" | "app_values" | "testing" | "first_task";

export type ReadinessRowKey = ReadinessStep | "work_trigger";

export type ReadinessRowState =
  | "done"
  | "missing"
  | "needs_admin"
  | "unknown"
  | "in_progress"
  | "review"
  | "failed"
  | "off"
  | "needs_fix"
  | "waiting";

export type ReadinessActionKind =
  | "copy_admin_request"
  | "open_environment"
  | "start_setup"
  | "fix_setup"
  | "review_proposal"
  | "view_job"
  | "create_issue";

export interface ReadinessAction {
  kind: ReadinessActionKind;
  label: string;
}

export interface ReadinessRow {
  key: ReadinessRowKey;
  required: boolean;
  state: ReadinessRowState;
  title: string;
  /** One plain sentence for the person. */
  says: string;
  action: ReadinessAction | null;
  missing_keys?: string[];
  job_id?: string;
  proposal_id?: string;
  pr_url?: string;
  trigger_label?: string;
}

/**
 * The repo page's setup status. Fixed rules over facts embry0 already
 * stores decide it, with no model call; a fact embry0 has not read stays
 * "unknown".
 */
export interface Readiness {
  repo: string;
  status: ReadinessStatus;
  steps_left: number;
  summary: string;
  /** The first required row a person can act on; null while embry0 works or when nothing is left. */
  next_step: ReadinessStep | null;
  rows: ReadinessRow[];
  needs_docker: boolean;
  facts_seen_at: string | null;
}

export interface SkipTestingResult {
  revision_id: number | null;
}

export async function fetchReadiness(fullRepo: string): Promise<Readiness> {
  const { data } = await api.get<Readiness>(`/repos/${fullRepo}/readiness`);
  return data;
}

/** Store a starter with testing off, so first tasks do not stop at the test step. 409 when a setup exists. */
export async function skipTesting(fullRepo: string): Promise<SkipTestingResult> {
  const { data } = await api.post<SkipTestingResult>(`/repos/${fullRepo}/setup/skip-testing`);
  return data;
}
