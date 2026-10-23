import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchGlobalEnv,
  setGlobalEnv,
  fetchRepoEnv,
  setRepoEnv,
  revealSecret,
  detectRepoEnv,
  fetchResolvedRepoEnv,
} from "@/api/environment";
import type { EnvVar } from "@/lib/types/environment";

export function useGlobalEnv() {
  return useQuery({ queryKey: ["global-env"], queryFn: fetchGlobalEnv });
}

export function useSetGlobalEnv() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (variables: EnvVar[]) => setGlobalEnv(variables),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["global-env"] }),
  });
}

/** A repo's environment variables: the key and fetch every reader of them shares. */
export function repoEnvQuery(fullRepo: string) {
  return {
    queryKey: ["repo-env", fullRepo],
    queryFn: () => fetchRepoEnv(fullRepo),
    enabled: !!fullRepo,
  };
}

export function useRepoEnv(fullRepo: string) {
  return useQuery(repoEnvQuery(fullRepo));
}

export function useSetRepoEnv() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fullRepo, variables }: { fullRepo: string; variables: EnvVar[] }) =>
      setRepoEnv(fullRepo, variables),
    onSuccess: (_, { fullRepo }) => qc.invalidateQueries({ queryKey: ["repo-env", fullRepo] }),
  });
}

export function useRevealSecret() {
  return useMutation({
    mutationFn: (params: { scope: "global" | "repo"; key: string; fullRepo?: string }) =>
      revealSecret(params.scope, params.key, params.fullRepo),
  });
}

export function useDetectRepoEnv(fullRepo: string) {
  return useQuery({
    queryKey: ["detect-env", fullRepo],
    queryFn: () => detectRepoEnv(fullRepo),
    enabled: !!fullRepo,
    staleTime: 60_000,
  });
}

/** The effective environment of a repo; read only while something shows it. */
export function resolvedRepoEnvQuery(fullRepo: string, enabled = true) {
  return {
    queryKey: ["repo-env", fullRepo, "resolved"],
    queryFn: () => fetchResolvedRepoEnv(fullRepo),
    enabled: !!fullRepo && enabled,
  };
}

export function useResolvedRepoEnv(fullRepo: string, enabled = true) {
  return useQuery(resolvedRepoEnvQuery(fullRepo, enabled));
}
