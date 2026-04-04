import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchAgents, fetchAgent, createAgent, updateAgent, deleteAgent, resetAgent } from "@/api/agents";
import type { AgentCreateRequest, AgentUpdateRequest } from "@/lib/types/agents";

export function useAgents() {
  return useQuery({ queryKey: ["agents"], queryFn: fetchAgents });
}

export function useAgent(type: string | null) {
  return useQuery({
    queryKey: ["agent", type],
    queryFn: () => fetchAgent(type!),
    enabled: !!type,
  });
}

export function useCreateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (agent: AgentCreateRequest) => createAgent(agent),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  });
}

export function useUpdateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ type, ...update }: AgentUpdateRequest & { type: string }) => updateAgent(type, update),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  });
}

export function useDeleteAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (type: string) => deleteAgent(type),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  });
}

export function useResetAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (type: string) => resetAgent(type),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agents"] }),
  });
}
