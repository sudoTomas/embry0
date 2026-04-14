import { api } from "./client";
import type { EnvVar, DetectedEnvVar } from "@/lib/types/environment";

export async function fetchGlobalEnv(): Promise<EnvVar[]> {
  const { data } = await api.get("/environment/global");
  return data.variables;
}

export async function setGlobalEnv(variables: EnvVar[]): Promise<void> {
  await api.put("/environment/global", { variables });
}

export async function fetchRepoEnv(owner: string, repo: string): Promise<EnvVar[]> {
  const { data } = await api.get(`/repos/${owner}/${repo}/environment`);
  return data.variables;
}

export async function setRepoEnv(owner: string, repo: string, variables: EnvVar[]): Promise<void> {
  await api.put(`/repos/${owner}/${repo}/environment`, { variables });
}

export async function deleteRepoEnvVar(owner: string, repo: string, key: string): Promise<void> {
  await api.delete(`/repos/${owner}/${repo}/environment/${key}`);
}

export async function revealSecret(
  scope: "global" | "repo",
  key: string,
  owner?: string,
  repo?: string,
): Promise<string> {
  const path =
    scope === "global"
      ? `/environment/global/${key}/reveal`
      : `/repos/${owner}/${repo}/environment/${key}/reveal`;
  const { data } = await api.get(path);
  return data.value;
}

export interface DetectRepoEnvResponse {
  source_file: string;
  variables: DetectedEnvVar[];
  unconfigured_count: number;
}

export async function detectRepoEnv(owner: string, repo: string): Promise<DetectRepoEnvResponse> {
  const { data } = await api.get(`/repos/${owner}/${repo}/environment/detect`);
  return data;
}
