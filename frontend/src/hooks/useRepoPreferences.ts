import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchRepoPreferences,
  setRepoPreferences,
  deleteRepoPreferences,
  type RepoPreferencesUpdate,
} from "@/api/repoPreferences";

export function useRepoPreferences(owner: string, repo: string) {
  return useQuery({
    queryKey: ["repo-preferences", owner, repo],
    queryFn: () => fetchRepoPreferences(owner, repo),
    enabled: !!owner && !!repo,
  });
}

export function useSetRepoPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      owner,
      repo,
      update,
    }: {
      owner: string;
      repo: string;
      update: RepoPreferencesUpdate;
    }) => setRepoPreferences(owner, repo, update),
    onSuccess: (_data, { owner, repo }) =>
      qc.invalidateQueries({ queryKey: ["repo-preferences", owner, repo] }),
  });
}

export function useDeleteRepoPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ owner, repo }: { owner: string; repo: string }) =>
      deleteRepoPreferences(owner, repo),
    onSuccess: (_data, { owner, repo }) =>
      qc.invalidateQueries({ queryKey: ["repo-preferences", owner, repo] }),
  });
}
