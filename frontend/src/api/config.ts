import { api } from "./client";
import type { ConfigResponse, ConfigUpdateRequest } from "@/lib/types/config";

export type { ConfigResponse, ConfigUpdateRequest };

export async function fetchConfig(): Promise<ConfigResponse> {
  const { data } = await api.get<ConfigResponse>("/config/budget");
  return data;
}

export async function updateConfig(config: ConfigUpdateRequest): Promise<ConfigResponse> {
  const { data } = await api.put<ConfigResponse>("/config/budget", config);
  return data;
}

// Context config
export interface ContextConfig {
  system_context: string;
  assistant_context: string;
}

export async function fetchGlobalContext(): Promise<ContextConfig> {
  const { data } = await api.get<ContextConfig>("/config/context");
  return data;
}

export async function updateGlobalContext(config: ContextConfig): Promise<void> {
  await api.put("/config/context", config);
}

// Per-repo context
export interface RepoContext {
  repo: string;
  system_context: string;
  assistant_context: string;
}

export async function fetchRepoContexts(): Promise<RepoContext[]> {
  const { data } = await api.get<RepoContext[]>("/config/context/repos");
  return data;
}

export async function updateRepoContext(repo: string, context: Omit<RepoContext, "repo">): Promise<void> {
  await api.put(`/config/context/repos/${encodeURIComponent(repo)}`, context);
}

export async function deleteRepoContext(repo: string): Promise<void> {
  await api.delete(`/config/context/repos/${encodeURIComponent(repo)}`);
}
