import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchIssues, fetchIssue, fetchIssueActivity,
  createIssue, updateIssue, deleteIssue, triageIssue, syncIssue,
} from "@/api/issues";
import type { CreateIssueRequest, IssueFilters, IssueResponse, IssueStatus, UpdateIssueRequest } from "@/lib/types";

export function useIssues(filters: IssueFilters = {}) {
  return useQuery({
    queryKey: ["issues", filters],
    queryFn: () => fetchIssues(filters),
    refetchInterval: 10_000,
  });
}

/** The most cards one board column loads (the API's page cap). */
export const BOARD_COLUMN_LIMIT = 200;

/**
 * The Issues board, fetched apart from the list's paging: one request per
 * status column, so each column counts every matching issue (the response's
 * total) instead of the issues on the list's current page.
 */
export function useIssueBoard(
  statuses: readonly IssueStatus[],
  filters: Pick<IssueFilters, "priority" | "repo" | "search">,
  enabled = true,
) {
  const results = useQueries({
    queries: statuses.map((status) => {
      const f: IssueFilters = { ...filters, status, limit: BOARD_COLUMN_LIMIT, offset: 0 };
      return { queryKey: ["issues", f], queryFn: () => fetchIssues(f), refetchInterval: 10_000, enabled };
    }),
  });
  const issues: IssueResponse[] = [];
  const totals: Partial<Record<IssueStatus, number>> = {};
  results.forEach((r, i) => {
    if (!r.data) return;
    issues.push(...r.data.issues);
    totals[statuses[i]] = r.data.total;
  });
  return {
    issues,
    totals,
    isLoading: results.some((r) => r.isLoading),
    isError: results.some((r) => r.isError),
    refetch: () => results.forEach((r) => r.refetch()),
  };
}

export function useIssue(issueId: string | undefined) {
  return useQuery({
    queryKey: ["issues", issueId],
    queryFn: () => fetchIssue(issueId!),
    enabled: !!issueId,
    refetchInterval: 10_000,
  });
}

export function useIssueActivity(issueId: string | undefined) {
  return useQuery({
    queryKey: ["issues", issueId, "activity"],
    queryFn: () => fetchIssueActivity(issueId!),
    enabled: !!issueId,
    refetchInterval: 15_000,
  });
}

export function useCreateIssue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: CreateIssueRequest) => createIssue(req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["issues"] }),
  });
}

export function useUpdateIssue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...req }: UpdateIssueRequest & { id: string }) => updateIssue(id, req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["issues"] }),
  });
}

export function useDeleteIssue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (issueId: string) => deleteIssue(issueId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["issues"] }),
  });
}

export function useTriageIssue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (issueId: string) => triageIssue(issueId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["issues"] }),
  });
}

export function useSyncIssue() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (issueId: string) => syncIssue(issueId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["issues"] }),
  });
}
