import { api } from "./client";

// The budget is the settings document's `budget` section (#171): read it with
// fetchEffectiveSettings and change it with setSettingsKeys (api/settings.ts),
// as hooks/useBudgetSettings.ts does. GET and PUT /config/budget are deprecated.

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
