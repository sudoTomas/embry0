export type IssueStatus = "open" | "triaging" | "awaiting_input" | "in_progress" | "closed" | "cancelled";
export type IssuePriority = "critical" | "high" | "medium" | "low";
// Channels the orchestrator can fan out agent ask-user questions to. Mirrors
// embry0.orchestration.state.AskUserChannel on the backend; the API responds
// with these as plain strings (see IssueResponse.notification_channels in
// embry0/api/schemas/issues.py).
export type NotificationChannel = "dashboard" | "telegram" | "github";

export interface IssueResponse {
  id: string;
  title: string;
  body: string;
  status: IssueStatus;
  priority: IssuePriority;
  labels: string[];
  repo: string | null;
  parent_issue_id: string | null;
  github_number: number | null;
  github_url: string | null;
  github_sync_enabled: boolean;
  github_synced_at: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  children_count: number;
  children_closed_count: number;
  jobs_count: number;
  active_agent: string | null;
  notification_channels: NotificationChannel[];
}

export interface IssueDetailResponse extends IssueResponse {
  children: IssueResponse[];
  jobs: Record<string, unknown>[];
}

export interface IssueListResponse {
  issues: IssueResponse[];
  total: number;
  offset: number;
  limit: number;
}

export interface CreateIssueRequest {
  title: string;
  body?: string;
  labels?: string[];
  priority?: IssuePriority;
  repo?: string | null;
  github_sync_enabled?: boolean;
  auto_triage?: boolean;
  notification_channels?: NotificationChannel[];
}

export interface UpdateIssueRequest {
  title?: string;
  body?: string;
  status?: IssueStatus;
  priority?: IssuePriority;
  labels?: string[];
  repo?: string | null;
  github_sync_enabled?: boolean;
  notification_channels?: NotificationChannel[];
}

export interface IssueFilters {
  status?: string;
  priority?: string;
  repo?: string;
  labels?: string;
  search?: string;
  parent_issue_id?: string;
  sort?: string;
  order?: string;
  limit?: number;
  offset?: number;
}

export interface ActivityEntry {
  id: number;
  action: string;
  actor: string;
  details: Record<string, unknown>;
  issue_id: string | null;
  created_at: string;
}
