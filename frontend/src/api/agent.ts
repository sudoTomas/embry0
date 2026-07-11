import axios from "axios";

// The Companion agent is a separate backend from the orchestrator, served behind
// the nginx reverse-proxy prefix `/agent`. Keeping a dedicated axios instance
// avoids the orchestrator client's `/api/v1` baseURL and any orchestrator-only
// auth header from leaking onto Companion requests.
export const agentApi = axios.create({
  baseURL: "/agent",
  timeout: 30_000,
  headers: {
    "X-Requested-With": "XMLHttpRequest",
  },
});

// The agent backend is an optional companion service. When a deployment has no
// /agent reverse-proxy, requests fall through to the SPA fallback and come back
// as HTTP 200 with an HTML body. Axios treats that as success, which would
// poison react-query caches with HTML strings and crash renderers expecting
// arrays/objects. Reject those responses so callers land on their existing
// error/empty states instead.
agentApi.interceptors.response.use((response) => {
  const contentType = String(response.headers?.["content-type"] ?? "");
  if (
    typeof response.data === "string" &&
    (contentType.includes("text/html") || response.data.trimStart().startsWith("<"))
  ) {
    return Promise.reject(
      new Error("Agent backend unavailable: /agent returned a non-JSON response"),
    );
  }
  return response;
});

// The agent's task ids are integers in the live API (e.g. `13`), but action
// routes and React keys treat them as strings. Accept both at the boundary;
// stringify at the edges that need a string.
export type AgentTaskId = string | number;

// Live task statuses observed from the agent: done / failed / stopped /
// running / queued. `dead_letter` is a client-side action target. Kept open
// (union | string) so an unseen status never breaks rendering — callers must
// not index a Record by status without a fallback.
export type AgentTaskStatus =
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "stopped"
  | "dead_letter";

export interface AgentTask {
  id: AgentTaskId;
  status: AgentTaskStatus | string;
  project?: string;
  title?: string;
  cost_usd?: number;
  created_at?: string;
  updated_at?: string;
  finished_at?: string;
}

export interface AgentTaskBlockedBy {
  id: AgentTaskId;
  blocked_by: ReadonlyArray<{
    id: AgentTaskId;
    status: AgentTaskStatus | string;
    title?: string;
  }>;
}

// GET /costs — per-provider cost/token rollups plus a daily-usage series.
// Each provider block is optional because the agent only emits providers it
// has data for.
export interface AgentProviderCost {
  real_cost_usd?: number;
  notional_cost_usd?: number;
  tokens_in: number;
  tokens_out: number;
  subscription?: string;
}

export interface AgentDailyUsage {
  day: string;
  tokens_in: number;
  tokens_out: number;
  tasks_completed: number;
}

export interface AgentReviewRollup {
  total: number;
  pass: number;
  warn: number;
  fail: number;
  needs_review: number;
}

export interface AgentCostsSummary {
  grok?: AgentProviderCost;
  claude?: AgentProviderCost;
  reviews?: AgentReviewRollup;
  daily_usage?: ReadonlyArray<AgentDailyUsage>;
}

// GET /stats — status counts (array of {status,count}), a live `running`
// gauge, and a list of recently-finished tasks.
export interface AgentStatusCount {
  status: string;
  count: number;
}

export interface AgentRecentTask {
  id: AgentTaskId;
  title?: string;
  project?: string;
  cost_usd?: number;
  finished_at?: string;
}

export interface AgentStats {
  counts: ReadonlyArray<AgentStatusCount>;
  running: number;
  recent: ReadonlyArray<AgentRecentTask>;
}

// GET /events — top-level array; `detail` is a JSON-encoded string the UI
// must parse defensively.
export interface AgentEvent {
  id: AgentTaskId;
  task_id?: AgentTaskId;
  event_type: string;
  detail?: string;
  created_at: string;
}

// GET /git-activity — { commits, repos }. `repos[]` carries GitHub repo
// metadata; `commits[]` shape is open until the agent populates it.
export interface AgentGitRepo {
  name: string;
  pushedAt?: string;
  openIssues?: number;
  defaultBranch?: string;
  url?: string;
}

export interface AgentGitActivity {
  commits: ReadonlyArray<Record<string, unknown>>;
  repos: ReadonlyArray<AgentGitRepo>;
}

export interface AgentProject {
  slug: string;
  name?: string;
  repo?: string;
}

// GET /routing-stats — by_model and by_phase are ARRAYS of objects, not maps.
export interface AgentRoutingModelRow {
  routed_model: string;
  count: number;
  success_rate?: number;
}

export interface AgentRoutingPhaseRow {
  phase: string;
  count: number;
  success_rate?: number;
}

export interface AgentRoutingStats {
  by_model: ReadonlyArray<AgentRoutingModelRow>;
  by_phase: ReadonlyArray<AgentRoutingPhaseRow>;
}

// GET /review-stats — by_type is an array; agreement_rate is a string ("N/A")
// OR a number.
export interface AgentReviewTypeRow {
  type: string;
  count: number;
}

export interface AgentReviewStats {
  by_type: ReadonlyArray<AgentReviewTypeRow>;
  agreement_rate: string | number;
  total_dual_reviews: number;
  agreed: number;
}

// GET /hardware — `ollama_models` is a JSON-encoded STRING that must be
// JSON.parse'd before use (guard against parse errors + non-array).
export interface AgentHardware {
  id?: number;
  hostname: string;
  total_memory_gb?: number;
  available_memory_gb?: number;
  gpu_info?: string;
  ollama_models?: string;
}

// GET /memories — top-level array. Item shape is open (live response is `[]`);
// scope/body are the fields the UI renders when present.
export interface AgentMemory {
  id: AgentTaskId;
  scope?: string;
  body?: string;
}

// Scanner proposals — Companion-pattern repo-scan output the operator triages and
// ships as issue-to-PR jobs. Severity is a 1-10 scalar; urgency tiers (Critical
// 8-10 / High 6-7 / Medium 4-5 / Low 1-3) are derived client-side.
export interface AgentProposal {
  id: string;
  title: string;
  repo?: string;
  severity?: number;
  status?: "pending" | "shipped" | "dismissed";
  summary?: string;
  created_at?: string;
  scored_at?: string;
}

export interface BatchShipResult {
  shipped: string[];
  skipped?: string[];
}

export interface AgentRepo {
  slug: string;
  branch?: string;
  dirty?: boolean;
  ahead?: number;
  behind?: number;
  last_commit?: string;
  pr_url?: string;
  pr_number?: number;
}

export type AgentNotificationLevel = "info" | "success" | "warning" | "error";

export interface AgentNotification {
  id: string;
  ts: string;
  title: string;
  body?: string;
  level?: AgentNotificationLevel;
  read?: boolean;
  /** Optional target the notification points the operator at. */
  href?: string;
}

export type InterpretIntent = "navigate" | "run" | "answer" | "unknown";

export interface InterpretResult {
  intent: InterpretIntent;
  message: string;
  /** When intent is "navigate", the dashboard route the palette should send the operator to. */
  url?: string;
}

export type FeedbackCategory = "bug" | "feature";
export type FeedbackLevel = "low" | "medium" | "high";

export interface FeedbackPayload {
  category: FeedbackCategory;
  severity: FeedbackLevel;
  urgency: FeedbackLevel;
  title: string;
  body: string;
  screenshot?: Blob;
}

export async function fetchTasks(): Promise<AgentTask[]> {
  const { data } = await agentApi.get<AgentTask[]>("/tasks");
  return data;
}

export async function fetchTaskBlockedBy(id: AgentTaskId): Promise<AgentTaskBlockedBy> {
  const { data } = await agentApi.get<AgentTaskBlockedBy>(
    `/tasks/${encodeURIComponent(String(id))}/blocked-by`,
  );
  return data;
}

export async function deployTask(id: AgentTaskId): Promise<AgentTask> {
  const { data } = await agentApi.post<AgentTask>(`/tasks/${encodeURIComponent(String(id))}/deploy`);
  return data;
}

export async function requeueTask(id: AgentTaskId): Promise<AgentTask> {
  const { data } = await agentApi.post<AgentTask>(`/tasks/${encodeURIComponent(String(id))}/requeue`);
  return data;
}

export async function retryTask(id: AgentTaskId): Promise<AgentTask> {
  const { data } = await agentApi.post<AgentTask>(`/tasks/${encodeURIComponent(String(id))}/retry`);
  return data;
}

export async function stopTask(id: AgentTaskId): Promise<AgentTask> {
  const { data } = await agentApi.post<AgentTask>(`/tasks/${encodeURIComponent(String(id))}/stop`);
  return data;
}

export async function deadLetterTask(id: AgentTaskId): Promise<AgentTask> {
  const { data } = await agentApi.post<AgentTask>(`/tasks/${encodeURIComponent(String(id))}/dead-letter`);
  return data;
}

export async function fetchCosts(): Promise<AgentCostsSummary> {
  const { data } = await agentApi.get<AgentCostsSummary>("/costs");
  return data;
}

export async function fetchStats(): Promise<AgentStats> {
  const { data } = await agentApi.get<AgentStats>("/stats");
  return data;
}

export async function fetchEvents(): Promise<AgentEvent[]> {
  const { data } = await agentApi.get<AgentEvent[]>("/events");
  return data;
}

export async function fetchGitActivity(): Promise<AgentGitActivity> {
  const { data } = await agentApi.get<AgentGitActivity>("/git-activity");
  return data;
}

export async function fetchProjects(): Promise<AgentProject[]> {
  const { data } = await agentApi.get<AgentProject[]>("/projects");
  return data;
}

export async function fetchRoutingStats(): Promise<AgentRoutingStats> {
  const { data } = await agentApi.get<AgentRoutingStats>("/routing-stats");
  return data;
}

export async function fetchReviewStats(): Promise<AgentReviewStats> {
  const { data } = await agentApi.get<AgentReviewStats>("/review-stats");
  return data;
}

export async function fetchHardware(): Promise<AgentHardware> {
  const { data } = await agentApi.get<AgentHardware>("/hardware");
  return data;
}

export async function fetchMemories(): Promise<AgentMemory[]> {
  const { data } = await agentApi.get<AgentMemory[]>("/memories");
  return data;
}

export async function fetchProposals(): Promise<AgentProposal[]> {
  const { data } = await agentApi.get<AgentProposal[]>("/proposals");
  return data;
}

export async function rescoreProposal(id: string): Promise<AgentProposal> {
  const { data } = await agentApi.post<AgentProposal>(
    `/proposals/${encodeURIComponent(id)}/rescore`,
  );
  return data;
}

export async function shipProposal(id: string): Promise<AgentProposal> {
  const { data } = await agentApi.post<AgentProposal>(
    `/proposals/${encodeURIComponent(id)}/ship`,
  );
  return data;
}

export async function batchShipProposals(ids: string[]): Promise<BatchShipResult> {
  const { data } = await agentApi.post<BatchShipResult>("/proposals/ship", { ids });
  return data;
}

export async function fetchRepos(): Promise<AgentRepo[]> {
  const { data } = await agentApi.get<AgentRepo[]>("/repos");
  return data;
}

export async function pushRepo(slug: string): Promise<AgentRepo> {
  const { data } = await agentApi.post<AgentRepo>(
    `/repos/${encodeURIComponent(slug)}/push`,
  );
  return data;
}

export async function pushRepoPr(slug: string): Promise<AgentRepo> {
  const { data } = await agentApi.post<AgentRepo>(
    `/repos/${encodeURIComponent(slug)}/push-pr`,
  );
  return data;
}

export async function mergeRepoPr(slug: string): Promise<AgentRepo> {
  const { data } = await agentApi.post<AgentRepo>(
    `/repos/${encodeURIComponent(slug)}/merge-pr`,
  );
  return data;
}

export async function fetchNotifications(): Promise<AgentNotification[]> {
  const { data } = await agentApi.get<AgentNotification[]>("/notifications");
  return data;
}

export async function markAllNotificationsRead(): Promise<void> {
  await agentApi.post("/notifications/read-all");
}

export async function interpretCommand(q: string): Promise<InterpretResult> {
  const { data } = await agentApi.post<InterpretResult>("/interpret", { q });
  return data;
}

// Posts the Phase-5 feedback FAB payload to the Companion agent. Body is
// multipart/form-data because `screenshot` is a Blob. We deliberately do NOT
// set a Content-Type header here: axios + the browser must set it themselves
// so the multipart boundary is correct. Passing `"multipart/form-data"`
// manually strips the boundary and breaks parsing on the server side.
export async function submitFeedback(payload: FeedbackPayload): Promise<void> {
  const form = new FormData();
  form.append("category", payload.category);
  form.append("severity", payload.severity);
  form.append("urgency", payload.urgency);
  form.append("title", payload.title);
  form.append("body", payload.body);
  if (payload.screenshot) {
    form.append("screenshot", payload.screenshot, "screenshot.png");
  }
  await agentApi.post("/feedback", form);
}
