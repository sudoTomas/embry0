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

// Budget config (explicit typed aliases)
export type BudgetConfig = ConfigResponse;

export async function fetchBudgetConfig(): Promise<BudgetConfig> {
  const { data } = await api.get<BudgetConfig>("/config/budget");
  return data;
}

export async function updateBudgetConfig(config: Partial<BudgetConfig>): Promise<void> {
  await api.put("/config/budget", config);
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
