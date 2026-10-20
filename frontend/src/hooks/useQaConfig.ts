import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { checkQaConfig, fetchQaConfig, saveQaConfig, deleteQaConfig } from "@/api/qaConfig";

/** A repo's stored setup: the key and fetch every reader of it shares. */
export function qaConfigQuery(fullRepo: string) {
  return {
    queryKey: ["qa-config", fullRepo],
    queryFn: () => fetchQaConfig(fullRepo),
    enabled: !!fullRepo,
    retry: false,
  };
}

/**
 * The stored qa.yaml text for a repo. `data === null` means no setup is
 * stored, so QA treats the repo as unconfigured.
 */
export function useQaConfig(fullRepo: string) {
  return useQuery(qaConfigQuery(fullRepo));
}

export function useSaveQaConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fullRepo, yamlText }: { fullRepo: string; yamlText: string }) =>
      saveQaConfig(fullRepo, yamlText),
    onSuccess: (_data, { fullRepo }) =>
      qc.invalidateQueries({ queryKey: ["qa-config", fullRepo] }),
  });
}

/** Run the free check on a setup's text. It stores nothing, so no query is invalidated. */
export function useCheckQaConfig() {
  return useMutation({
    mutationFn: ({ fullRepo, yamlText }: { fullRepo: string; yamlText: string }) =>
      checkQaConfig(fullRepo, yamlText),
  });
}

export function useDeleteQaConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fullRepo }: { fullRepo: string }) =>
      deleteQaConfig(fullRepo),
    onSuccess: (_data, { fullRepo }) =>
      qc.invalidateQueries({ queryKey: ["qa-config", fullRepo] }),
  });
}
