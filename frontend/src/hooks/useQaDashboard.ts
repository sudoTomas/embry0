import { useQuery } from "@tanstack/react-query";
import {
  fetchQaAppHistory,
  fetchQaRepos,
  fetchQaRun,
  fetchQaRunApp,
  fetchQaRunsForRepo,
} from "@/api/qaDashboard";

export function useQaRepos(limit = 50) {
  return useQuery({
    queryKey: ["qa-dashboard", "repos", limit],
    queryFn: () => fetchQaRepos(limit),
    refetchInterval: 30_000,
  });
}

export function useQaRunsForRepo(
  repo: string | undefined,
  opts: { limit?: number; offset?: number } = {},
) {
  return useQuery({
    queryKey: ["qa-dashboard", "repos", repo, "runs", opts.limit ?? 50, opts.offset ?? 0],
    queryFn: () => fetchQaRunsForRepo(repo!, opts),
    enabled: !!repo,
    refetchInterval: 30_000,
  });
}

export function useQaRun(runId: string | undefined) {
  return useQuery({
    queryKey: ["qa-dashboard", "runs", runId],
    queryFn: () => fetchQaRun(runId!),
    enabled: !!runId,
    refetchInterval: 15_000,
  });
}

export function useQaRunApp(runId: string | undefined, app: string | undefined) {
  return useQuery({
    queryKey: ["qa-dashboard", "runs", runId, "apps", app],
    queryFn: () => fetchQaRunApp(runId!, app!),
    enabled: !!runId && !!app,
  });
}

export function useQaAppHistory(
  repo: string | undefined,
  app: string | undefined,
  limit = 20,
) {
  return useQuery({
    queryKey: ["qa-dashboard", "repos", repo, "apps", app, "history", limit],
    queryFn: () => fetchQaAppHistory(repo!, app!, limit),
    enabled: !!repo && !!app,
    refetchInterval: 30_000,
  });
}
