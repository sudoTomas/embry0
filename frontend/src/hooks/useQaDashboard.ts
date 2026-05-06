import { useQuery } from "@tanstack/react-query";
import {
  fetchAffectedSet,
  fetchCacheAnalytics,
  fetchQaAppHistory,
  fetchQaRepos,
  fetchQaRun,
  fetchQaRunApp,
  fetchQaRunsForRepo,
  listAppArtifacts,
  listProviderOverrides,
  type ArtifactKind,
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

/**
 * Phase 5D: subscribe to a run's affected-set snapshot. Stale-time of 60s
 * because the row is written once at fan-out time and never updated for
 * the life of the run — no need to poll.
 */
export function useAffectedSet(runId: string | undefined) {
  return useQuery({
    queryKey: ["qa-dashboard", "affected-set", runId],
    queryFn: () => fetchAffectedSet(runId!),
    enabled: !!runId,
    staleTime: 60_000,
  });
}

/**
 * Phase 5E: subscribe to a repo's cache analytics aggregate.
 *
 * The rollup is a 30-day aggregate that changes slowly — `staleTime` of
 * 5 minutes keeps the panel responsive without burning needless API
 * traffic, and we deliberately do NOT poll on an interval (a manual
 * refetch or repo switch is enough to refresh). The hook returns
 * `disabled` until `repo` is defined so route params can resolve first.
 */
export function useCacheAnalytics(
  repo: string | undefined,
  windowDays = 30,
) {
  return useQuery({
    queryKey: ["qa-dashboard", "cache-analytics", repo, windowDays],
    queryFn: () => fetchCacheAnalytics(repo!, windowDays),
    enabled: !!repo,
    staleTime: 5 * 60_000,
  });
}

/**
 * Phase 5G: subscribe to the per-repo workspace_provider override list.
 *
 * 30s stale time keeps the admin page responsive without burning API
 * traffic — overrides change only when an operator submits the form,
 * and the mutation paths invalidate this query themselves.
 */
export function useProviderOverrides() {
  return useQuery({
    queryKey: ["qa-dashboard", "provider-overrides"],
    queryFn: listProviderOverrides,
    staleTime: 30_000,
  });
}

export function useAppArtifacts(
  runId: string | undefined,
  app: string | undefined,
  kind: ArtifactKind,
) {
  return useQuery({
    queryKey: ["qa-artifacts", runId, app, kind],
    queryFn: () => listAppArtifacts(runId!, app!, kind),
    enabled: !!runId && !!app,
    staleTime: 30_000,
    // Poll at 15s for parity with `useQaRun` — while a job is still running,
    // screenshots / HARs / console logs arrive incrementally and the panel
    // should pick them up without a full page reload.
    refetchInterval: 15_000,
  });
}
