import type { IntegrationConfig, IntegrationConfigUpdate } from "@/lib/types/integrations";
import { api } from "./client";

export async function fetchIntegrationConfig(): Promise<IntegrationConfig> {
  const { data } = await api.get<IntegrationConfig>("/config/integrations");
  return data;
}

export async function updateIntegrationConfig(config: IntegrationConfigUpdate): Promise<IntegrationConfig> {
  const { data } = await api.put<IntegrationConfig>("/config/integrations", config);
  return data;
}
