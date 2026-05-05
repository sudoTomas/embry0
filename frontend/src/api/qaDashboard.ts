import { api } from "./client";
import type {
  AppHistoryItem,
  AppResult,
  RepoEntry,
  RunDetail,
  RunListItem,
} from "@/lib/types";

export async function fetchQaRepos(limit = 50): Promise<RepoEntry[]> {
  const { data } = await api.get<RepoEntry[]>("/qa/repos", {
    params: { limit },
  });
  return data;
}

export async function fetchQaRunsForRepo(
  repo: string,
  opts: { limit?: number; offset?: number } = {},
): Promise<RunListItem[]> {
  const { data } = await api.get<RunListItem[]>(
    `/qa/repos/${encodeURIComponent(repo)}/runs`,
    { params: { limit: opts.limit ?? 50, offset: opts.offset ?? 0 } },
  );
  return data;
}

export async function fetchQaAppHistory(
  repo: string,
  app: string,
  limit = 20,
): Promise<AppHistoryItem[]> {
  const { data } = await api.get<AppHistoryItem[]>(
    `/qa/repos/${encodeURIComponent(repo)}/apps/${encodeURIComponent(app)}/history`,
    { params: { limit } },
  );
  return data;
}

export async function fetchQaRun(runId: string): Promise<RunDetail> {
  const { data } = await api.get<RunDetail>(
    `/qa/runs/${encodeURIComponent(runId)}`,
  );
  return data;
}

export async function fetchQaRunApp(
  runId: string,
  app: string,
): Promise<AppResult> {
  const { data } = await api.get<AppResult>(
    `/qa/runs/${encodeURIComponent(runId)}/apps/${encodeURIComponent(app)}`,
  );
  return data;
}
