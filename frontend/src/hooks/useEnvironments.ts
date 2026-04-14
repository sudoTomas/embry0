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

export function useRepoEnv(owner: string, repo: string) {
  return useQuery({
    queryKey: ["repo-env", owner, repo],
    queryFn: () => fetchRepoEnv(owner, repo),
    enabled: !!owner && !!repo,
  });
}

export function useSetRepoEnv() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ owner, repo, variables }: { owner: string; repo: string; variables: EnvVar[] }) =>
      setRepoEnv(owner, repo, variables),
    onSuccess: (_, { owner, repo }) => qc.invalidateQueries({ queryKey: ["repo-env", owner, repo] }),
  });
}

export function useRevealSecret() {
  return useMutation({
    mutationFn: (params: { scope: "global" | "repo"; key: string; owner?: string; repo?: string }) =>
      revealSecret(params.scope, params.key, params.owner, params.repo),
  });
}

export function useDetectRepoEnv(owner: string, repo: string) {
  return useQuery({
    queryKey: ["detect-env", owner, repo],
    queryFn: () => detectRepoEnv(owner, repo),
    enabled: !!owner && !!repo,
    staleTime: 60_000,
  });
}
