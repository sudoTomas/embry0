import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchRepoPreferences,
  setRepoPreferences,
  deleteRepoPreferences,
  type RepoPreferencesUpdate,
} from "@/api/repoPreferences";

/** A repo's preferences: the key and fetch every reader of them shares. */
export function repoPreferencesQuery(fullRepo: string) {
  return {
    queryKey: ["repo-preferences", fullRepo],
    queryFn: () => fetchRepoPreferences(fullRepo),
    enabled: !!fullRepo,
  };
}

export function useRepoPreferences(fullRepo: string) {
  return useQuery(repoPreferencesQuery(fullRepo));
}

export function useSetRepoPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      fullRepo,
      update,
    }: {
      fullRepo: string;
      update: RepoPreferencesUpdate;
    }) => setRepoPreferences(fullRepo, update),
    onSuccess: (_data, { fullRepo }) =>
      qc.invalidateQueries({ queryKey: ["repo-preferences", fullRepo] }),
  });
}

export function useDeleteRepoPreferences() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fullRepo }: { fullRepo: string }) =>
      deleteRepoPreferences(fullRepo),
    onSuccess: (_data, { fullRepo }) =>
      qc.invalidateQueries({ queryKey: ["repo-preferences", fullRepo] }),
  });
}
