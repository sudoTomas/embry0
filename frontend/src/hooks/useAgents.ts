import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
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

/** A write to one agent stales both the list and that agent's form query
 * (["agent", type]); with the app's 5-minute staleTime a form reopened after
 * a save would otherwise show, and re-save, the old values. */
function invalidateAgent(qc: QueryClient, type: string) {
  qc.invalidateQueries({ queryKey: ["agents"] });
  qc.invalidateQueries({ queryKey: ["agent", type] });
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
    onSuccess: (_data, { type }) => invalidateAgent(qc, type),
  });
}

export function useDeleteAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (type: string) => deleteAgent(type),
    onSuccess: (_data, type) => invalidateAgent(qc, type),
  });
}

export function useResetAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (type: string) => resetAgent(type),
    onSuccess: (_data, type) => invalidateAgent(qc, type),
  });
}
