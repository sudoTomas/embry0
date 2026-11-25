import type {
  ProviderUsage,
  ProviderConfig,
  ProviderConfigUpdate,
  ConnectionTestResult,
  ModelsCatalog,
  CatalogueProvider,
  ProviderProbeResult,
  ProviderCatalogStatus,
} from "@/lib/types/provider";
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

// --- RAV-1297: complete catalog + live per-provider connection test ---

export async function fetchModelsCatalog(): Promise<ModelsCatalog> {
  const { data } = await api.get<ModelsCatalog>("/config/models");
  return data;
}

/**
 * Every provider of the provider catalogue in force, in its order, with whether it is enabled
 * (GET /config/providers/catalog, which any key reads). The LLM providers a credential can be for.
 */
export async function fetchCatalogueProviders(): Promise<CatalogueProvider[]> {
  const { data } = await api.get<{ catalogue?: { providers?: Record<string, { enabled?: boolean }> } }>(
    "/config/providers/catalog",
  );
  return Object.entries(data.catalogue?.providers ?? {}).map(([name, provider]) => ({
    name,
    enabled: provider.enabled === true,
  }));
}

export async function testCatalogProvider(name: string): Promise<ProviderProbeResult> {
  const { data } = await api.post<ProviderProbeResult>(
    `/config/providers/${encodeURIComponent(name)}/test`,
  );
  return data;
}

/** What the provider says its key has spent; only OpenRouter answers (404 for the rest). */
export async function fetchProviderUsage(name: string, fresh = false): Promise<ProviderUsage> {
  const { data } = await api.get<ProviderUsage>(`/config/providers/${encodeURIComponent(name)}/usage`, {
    params: fresh ? { fresh: true } : {},
  });
  return data;
}

/** Read OpenRouter's model list now (it is read daily by itself); a failed read keeps the list in force. */
export async function refreshOpenRouterCatalog(): Promise<ProviderCatalogStatus> {
  const { data } = await api.post<ProviderCatalogStatus>("/config/providers/openrouter/catalog/refresh");
  return data;
}
