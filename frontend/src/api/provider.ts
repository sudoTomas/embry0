import type { ProviderConfig, ProviderConfigUpdate, ConnectionTestResult } from "@/lib/types/provider";
import { api } from "./client";

export async function fetchProviderConfig(): Promise<ProviderConfig> {
  const { data } = await api.get<ProviderConfig>("/config/provider");
  return data;
}

export async function updateProviderConfig(config: ProviderConfigUpdate): Promise<ProviderConfig> {
  const { data } = await api.put<ProviderConfig>("/config/provider", config);
  return data;
}

export async function testProviderConnection(): Promise<ConnectionTestResult> {
  const { data } = await api.post<ConnectionTestResult>("/config/provider/test");
  return data;
}
