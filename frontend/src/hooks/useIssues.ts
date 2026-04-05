import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchIssues, fetchIssue, fetchIssueActivity,
  createIssue, updateIssue, deleteIssue, triageIssue, syncIssue,
} from "@/api/issues";
import type { CreateIssueRequest, IssueFilters, UpdateIssueRequest } from "@/lib/types";

export function useIssues(filters: IssueFilters = {}) {
  return useQuery({
    queryKey: ["issues", filters],
    queryFn: () => fetchIssues(filters),
    refetchInterval: 10_000,
  });
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
