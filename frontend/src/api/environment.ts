import { api } from "./client";
import { repoPath } from "./repoPath";
import type { EnvVar, DetectedEnvVar } from "@/lib/types/environment";

export async function fetchGlobalEnv(): Promise<EnvVar[]> {
  const { data } = await api.get("/environment/global");
  return data.variables;
}

export async function setGlobalEnv(variables: EnvVar[]): Promise<void> {
  // A 422 (a reserved key, #543) is shown by the caller, not the global toast.
  await api.put("/environment/global", { variables }, { suppressValidationToast: true });
}

export async function fetchRepoEnv(fullRepo: string): Promise<EnvVar[]> {
  const { data } = await api.get(repoPath(fullRepo, "environment"));
  return data.variables;
}

export async function setRepoEnv(fullRepo: string, variables: EnvVar[]): Promise<void> {
  await api.put(repoPath(fullRepo, "environment"), { variables }, { suppressValidationToast: true });
}

export async function deleteRepoEnvVar(fullRepo: string, key: string): Promise<void> {
  await api.delete(repoPath(fullRepo, `environment/${key}`));
}

export async function revealSecret(
  scope: "global" | "repo",
  key: string,
  fullRepo?: string,
): Promise<string> {
  if (scope === "repo" && fullRepo === undefined) {
    throw new Error("revealSecret: a repository's secret is read with its repository");
  }
  const path =
    scope === "repo" && fullRepo !== undefined
      ? repoPath(fullRepo, `environment/${key}/reveal`)
      : `/environment/global/${key}/reveal`;
  const { data } = await api.get(path);
  return data.value;
}

export interface DetectRepoEnvResponse {
  source_file: string;
  variables: DetectedEnvVar[];
  unconfigured_count: number;
}

export async function detectRepoEnv(fullRepo: string): Promise<DetectRepoEnvResponse> {
  const { data } = await api.get(repoPath(fullRepo, "environment/detect"));
  return data;
}

/** The variables a sandbox for this repository gets: global ones with the repo's written over them, secrets masked. */
export async function fetchResolvedRepoEnv(fullRepo: string): Promise<EnvVar[]> {
  const { data } = await api.get(repoPath(fullRepo, "environment/resolve"));
  return data.variables;
}
