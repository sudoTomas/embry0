import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchIntegrationConfig, updateIntegrationConfig } from "@/api/integrations";
import type { IntegrationConfigUpdate } from "@/lib/types/integrations";

export function useIntegrationConfig() {
  return useQuery({ queryKey: ["integration-config"], queryFn: fetchIntegrationConfig });
}

export function useUpdateIntegrationConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (config: IntegrationConfigUpdate) => updateIntegrationConfig(config),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["integration-config"] }),
  });
}
