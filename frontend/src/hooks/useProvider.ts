import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchProviderConfig,
  updateProviderConfig,
  testProviderConnection,
  fetchModelsCatalog,
  testCatalogProvider,
} from "@/api/provider";
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

export function useModelsCatalog() {
  // Same key as useModels so the two consumers share one cache entry.
  return useQuery({ queryKey: ["config", "models"], queryFn: fetchModelsCatalog });
}

export function useTestCatalogProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => testCatalogProvider(name),
    // The test is audited and GET /config/models reports it back as last_probe.
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config", "models"] }),
  });
}
