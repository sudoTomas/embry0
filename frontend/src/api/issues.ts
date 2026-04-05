import { api } from "./client";
import type {
  CreateIssueRequest,
  IssueDetailResponse,
  IssueFilters,
  IssueListResponse,
  UpdateIssueRequest,
  ActivityEntry,
} from "@/lib/types";

export async function fetchIssues(filters: IssueFilters = {}): Promise<IssueListResponse> {
  const { data } = await api.get("/issues", { params: filters });
  return data;
}

export async function fetchIssue(issueId: string): Promise<IssueDetailResponse> {
  const { data } = await api.get(`/issues/${issueId}`);
  return data;
}

export async function createIssue(req: CreateIssueRequest): Promise<IssueDetailResponse> {
  const { data } = await api.post("/issues", req);
  return data;
}

export async function updateIssue(issueId: string, req: UpdateIssueRequest): Promise<IssueDetailResponse> {
  const { data } = await api.put(`/issues/${issueId}`, req);
  return data;
}

export async function deleteIssue(issueId: string): Promise<void> {
  await api.delete(`/issues/${issueId}`);
}

export async function triageIssue(issueId: string): Promise<IssueDetailResponse> {
  const { data } = await api.post(`/issues/${issueId}/triage`);
  return data;
}

export async function syncIssue(issueId: string): Promise<IssueDetailResponse> {
  const { data } = await api.post(`/issues/${issueId}/sync`);
  return data;
}

export async function fetchIssueActivity(issueId: string): Promise<ActivityEntry[]> {
  const { data } = await api.get(`/issues/${issueId}/activity`);
  return data;
}
