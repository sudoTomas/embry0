import { useQuery } from "@tanstack/react-query";
import { fetchRepos, type RegisteredRepo } from "@/api/repos";

// RAV-1294: registry list shared by the RepoPicker and any surface that
// wants the fleet with env-var annotations.

export function useRegisteredRepos(opts: { refetchInterval?: number; enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ["repos"],
    queryFn: () => fetchRepos(),
    refetchInterval: opts.refetchInterval,
    enabled: opts.enabled,
  });
}

export function defaultRepoAnnotation(r: RegisteredRepo): string {
  if (!r.env_var_count) return r.repo;
  const secrets = r.env_secret_count
    ? `, ${r.env_secret_count} secret${r.env_secret_count !== 1 ? "s" : ""}`
    : "";
  return `${r.repo} (${r.env_var_count} var${r.env_var_count !== 1 ? "s" : ""}${secrets})`;
}
