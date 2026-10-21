import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchGlobalEnv,
  setGlobalEnv,
  fetchRepoEnv,
  setRepoEnv,
  revealSecret,
  detectRepoEnv,
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
