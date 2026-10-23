import { api } from "./client";
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
  const { data } = await api.get(`/repos/${fullRepo}/environment`);
  return data.variables;
}

export async function setRepoEnv(fullRepo: string, variables: EnvVar[]): Promise<void> {
  await api.put(`/repos/${fullRepo}/environment`, { variables }, { suppressValidationToast: true });
}

export async function deleteRepoEnvVar(fullRepo: string, key: string): Promise<void> {
  await api.delete(`/repos/${fullRepo}/environment/${key}`);
}

export async function revealSecret(
  scope: "global" | "repo",
  key: string,
  fullRepo?: string,
): Promise<string> {
  const path =
    scope === "global"
      ? `/environment/global/${key}/reveal`
      : `/repos/${fullRepo}/environment/${key}/reveal`;
  const { data } = await api.get(path);
  return data.value;
}

export interface DetectRepoEnvResponse {
  source_file: string;
  variables: DetectedEnvVar[];
  unconfigured_count: number;
}

export async function detectRepoEnv(fullRepo: string): Promise<DetectRepoEnvResponse> {
  const { data } = await api.get(`/repos/${fullRepo}/environment/detect`);
  return data;
}

/** The variables a sandbox for this repository gets: global ones with the repo's written over them, secrets masked. */
export async function fetchResolvedRepoEnv(fullRepo: string): Promise<EnvVar[]> {
  const { data } = await api.get(`/repos/${fullRepo}/environment/resolve`);
  return data.variables;
}
