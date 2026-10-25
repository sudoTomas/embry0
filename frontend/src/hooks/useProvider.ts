import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchProviderConfig,
  updateProviderConfig,
  testProviderConnection,
  fetchModelsCatalog,
  testCatalogProvider,
  fetchProviderUsage,
} from "@/api/provider";
import { addProviderKey } from "@/api/credentials";
import type { LlmProviderName } from "@/lib/llmProviders";
import type { ProviderConfigUpdate } from "@/lib/types/provider";

export function useProviderConfig() {
  return useQuery({ queryKey: ["provider-config"], queryFn: fetchProviderConfig });
}

export function useUpdateProviderConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (config: ProviderConfigUpdate) => updateProviderConfig(config),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["provider-config"] });
      // RAV-1297: tier + enablement edits change the catalog every model
      // dropdown reads (useModels shares this key).
      qc.invalidateQueries({ queryKey: ["config", "models"] });
    },
  });
}

export function useTestProviderConnection() {
  return useMutation({ mutationFn: testProviderConnection });
}

// --- RAV-1297: LLM Providers page ---

/** The models catalog: the key and fetch every reader of it shares. */
export function modelsCatalogQuery() {
  // Same key as useModels so the two consumers share one cache entry.
  return { queryKey: ["config", "models"], queryFn: fetchModelsCatalog };
}

export function useModelsCatalog() {
  return useQuery(modelsCatalogQuery());
}

export function useTestCatalogProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => testCatalogProvider(name),
    // The test is audited and GET /config/models reports it back as last_probe.
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config", "models"] }),
  });
}

/** What a provider says its key has spent (OpenRouter); read every five minutes, as the server keeps it. */
export function useProviderUsage(name: string, enabled: boolean) {
  return useQuery({
    queryKey: ["config", "providers", name, "usage"],
    queryFn: () => fetchProviderUsage(name),
    enabled,
    staleTime: 300_000,
    refetchInterval: 300_000,
  });
}

/** Store a key from a provider's card and make it the provider's global binding. */
export function useAddProviderKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ provider, ...body }: { provider: LlmProviderName; secret: string; label: string; baseUrl?: string }) =>
      addProviderKey(provider, body),
    onSuccess: (_created, { provider }) => {
      qc.invalidateQueries({ queryKey: ["config", "models"] });
      qc.invalidateQueries({ queryKey: ["config", "providers", provider, "usage"] });
      qc.invalidateQueries({ queryKey: ["credentials"] });
    },
  });
}
