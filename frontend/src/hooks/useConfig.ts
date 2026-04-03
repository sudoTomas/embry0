import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchConfig, updateConfig } from "@/api/config";
import type { ConfigUpdateRequest } from "@/lib/types";

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: fetchConfig,
  });
}

export function useUpdateConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: ConfigUpdateRequest) => updateConfig(req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config"] }),
  });
}
