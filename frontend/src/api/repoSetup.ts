import { api } from "./client";
import { isNotFound } from "./errorDetail";

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

/** What the free check of a test setup asks a person to see (PR 4's warning codes). */
export type QaCheckWarningCode =
  | "live_address"
  | "deployed_target"
  | "deployed_without_guardrails"
  | "needs_docker"
  | "testing_off"
  | "login_value_missing";

export interface QaCheckProblem {
  where: string;
  plain: string;
  fix: string;
}

export interface QaCheckWarning {
  code: QaCheckWarningCode;
  plain: string;
  /** A person must tick it before the setup is applied. */
  confirm: boolean;
}

export interface QaCheckApp {
  name: string;
  target: "managed" | "deployed";
  start: string | null;
  address: string;
  address_is_local: boolean;
  criteria: string[];
  guardrails: number;
  machine: string;
  machine_has_browser: boolean;
}

export interface QaCheckSummary {
  when: "auto" | "always" | "never";
  when_plain: string;
  apps: QaCheckApp[];
}

/**
 * The free check of a test setup (no model, no forge call). A copy of the
 * settings lane's type in api/qaConfig.ts; merge the two once both land.
 */
export interface QaCheckResult {
  ok: boolean;
  problems: QaCheckProblem[];
  warnings: QaCheckWarning[];
  summary: QaCheckSummary | null;
}

/** Recorded facts about setup runs on this embry0, never an estimate. */
export interface SetupCost {
  /** Finished setup runs the median is taken over (the newest 20). */
  recorded_runs: number;
  typical_cost_usd: number | null;
  per_job_budget_usd: number;
  model: string;
  /** The AI account that pays; null when it does not resolve. */
  paid_from: string | null;
}

export interface StartSetupResult {
  proposal_id: string;
  job_id: string;
}

export type SetupProposalStatus = "running" | "pending" | "applied" | "rejected" | "stale" | "failed";

/** The onboarding agent's boot and ready-check run: each app's outcome ("passed" when it answered). */
export interface SetupSmoke {
  results?: Record<string, string>;
  failures?: string[];
  skipped?: boolean;
}

/** A test setup the onboarding agent proposed, for a person to apply or turn down. */
export interface SetupProposal {
  id: string;
  repo: string;
  status: SetupProposalStatus;
  job_id: string | null;
  job_status: string | null;
  created_by: string;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  notes_md: string;
  failure: string;
  smoke: SetupSmoke | null;
  proposed_yaml: string | null;
  /** The stored setup now; null when there is none. */
  current_yaml: string | null;
  /** The free check of the proposed text; null when it was not run. */
  check: QaCheckResult | null;
}

export interface ApplyProposalResult {
  revision_id: number | null;
}

/** A test setup waiting for a person to review it (GET /setup/proposals?status=pending). */
export interface PendingSetupProposal {
  id: string;
  repo: string;
  created_at: string;
}

export async function fetchSetupCost(fullRepo: string): Promise<SetupCost> {
  const { data } = await api.get<SetupCost>(`/repos/${fullRepo}/setup/cost`);
  return data;
}

/**
 * Start the onboarding agent in propose mode. Only the consent dialog's
 * Start sends this; it shows any refusal itself (409 while a run is live).
 */
export async function startSetup(fullRepo: string): Promise<StartSetupResult> {
  const { data } = await api.post<StartSetupResult>(
    `/repos/${fullRepo}/setup/propose`,
    { confirm_spend: true },
    { suppressValidationToast: true, suppressErrorToast: true },
  );
  return data;
}

/** The latest proposal for a repository, or null when it has none. */
export async function fetchProposal(fullRepo: string): Promise<SetupProposal | null> {
  try {
    const { data } = await api.get<SetupProposal>(`/repos/${fullRepo}/setup/proposal`);
    return data;
  } catch (error) {
    if (isNotFound(error)) return null;
    throw error;
  }
}

/**
 * Apply a pending proposal, naming every warning the person ticked. 409 when
 * the setup changed since the proposal (it is then stale); 422 when the check
 * fails or a warning was not ticked. The card shows either reason.
 */
export async function applyProposal(
  fullRepo: string,
  proposalId: string,
  acknowledged: QaCheckWarningCode[],
): Promise<ApplyProposalResult> {
  const { data } = await api.post<ApplyProposalResult>(
    `/repos/${fullRepo}/setup/proposal/${proposalId}/apply`,
    { acknowledged },
    { suppressValidationToast: true },
  );
  return data;
}

export async function rejectProposal(fullRepo: string, proposalId: string): Promise<void> {
  await api.post(`/repos/${fullRepo}/setup/proposal/${proposalId}/reject`);
}

/** Every repository's test setup waiting for review, for the Overview's Needs-you brief. */
export async function fetchPendingProposals(): Promise<PendingSetupProposal[]> {
  const { data } = await api.get<{ proposals: PendingSetupProposal[] }>("/setup/proposals", {
    params: { status: "pending" },
  });
  return data.proposals;
}
