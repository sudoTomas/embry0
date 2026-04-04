import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchProviderConfig, updateProviderConfig, testProviderConnection } from "@/api/provider";
import type { ProviderConfigUpdate } from "@/lib/types/provider";

export function useProviderConfig() {
  return useQuery({ queryKey: ["provider-config"], queryFn: fetchProviderConfig });
}

export function useUpdateProviderConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (config: ProviderConfigUpdate) => updateProviderConfig(config),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["provider-config"] }),
  });
}

export function useTestProviderConnection() {
  return useMutation({ mutationFn: testProviderConnection });
}
