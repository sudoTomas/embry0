import axios from "axios";

// The companion agent is a separate backend from the orchestrator, served behind
// the nginx reverse-proxy prefix `/agent`. Keeping a dedicated axios instance
// avoids the orchestrator client's `/api/v1` baseURL and any orchestrator-only
// auth header from leaking onto companion requests.
export const agentApi = axios.create({
  baseURL: "/agent",
  timeout: 30_000,
  headers: {
    "X-Requested-With": "XMLHttpRequest",
  },
});

export type AgentTaskStatus =
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "dead_letter";

export interface AgentTask {
  id: string;
  status: AgentTaskStatus;
  project?: string;
  title?: string;
  created_at?: string;
  updated_at?: string;
}

export interface AgentTaskBlockedBy {
  id: string;
  blocked_by: ReadonlyArray<{
    id: string;
    status: AgentTaskStatus;
    title?: string;
  }>;
}

export interface AgentCostsSummary {
  total_usd: number;
  by_project?: Record<string, number>;
  top_tasks?: Array<{ id: string; usd: number }>;
}

export interface AgentStats {
  running: number;
  queued: number;
  done: number;
  failed: number;
  dead_letter?: number;
}

export interface AgentEvent {
  id: string;
  type: string;
  task_id?: string;
  ts: string;
  payload?: Record<string, unknown>;
}

export interface AgentGitActivity {
  id: string;
  repo: string;
  branch?: string;
  action: string;
  ts: string;
  sha?: string;
  message?: string;
  pr_number?: number;
  pr_url?: string;
  author?: string;
}

export interface AgentProject {
  slug: string;
  name?: string;
  repo?: string;
}

export interface AgentRoutingStats {
  by_model: Record<string, number>;
}

export interface AgentReviewStats {
  pass: number;
  fail: number;
  warn?: number;
}

export interface AgentHardware {
  host: string;
  cpu_pct?: number;
  mem_pct?: number;
  gpus?: Array<{ name: string; mem_used_mb?: number }>;
}

export interface AgentMemory {
  id: string;
  scope?: string;
  body?: string;
}

// Scanner proposals — companion-pattern repo-scan output the operator triages and
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

export async function fetchTaskBlockedBy(id: string): Promise<AgentTaskBlockedBy> {
  const { data } = await agentApi.get<AgentTaskBlockedBy>(
    `/tasks/${encodeURIComponent(id)}/blocked-by`,
  );
  return data;
}

export async function deployTask(id: string): Promise<AgentTask> {
  const { data } = await agentApi.post<AgentTask>(`/tasks/${encodeURIComponent(id)}/deploy`);
  return data;
}

export async function requeueTask(id: string): Promise<AgentTask> {
  const { data } = await agentApi.post<AgentTask>(`/tasks/${encodeURIComponent(id)}/requeue`);
  return data;
}

export async function retryTask(id: string): Promise<AgentTask> {
  const { data } = await agentApi.post<AgentTask>(`/tasks/${encodeURIComponent(id)}/retry`);
  return data;
}

export async function stopTask(id: string): Promise<AgentTask> {
  const { data } = await agentApi.post<AgentTask>(`/tasks/${encodeURIComponent(id)}/stop`);
  return data;
}

export async function deadLetterTask(id: string): Promise<AgentTask> {
  const { data } = await agentApi.post<AgentTask>(`/tasks/${encodeURIComponent(id)}/dead-letter`);
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

export async function fetchGitActivity(): Promise<AgentGitActivity[]> {
  const { data } = await agentApi.get<AgentGitActivity[]>("/git-activity");
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

// Posts the Phase-5 feedback FAB payload to the companion agent. Body is
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
