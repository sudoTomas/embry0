import type { AgentDefinition, AgentCreateRequest, AgentUpdateRequest } from "@/lib/types/agents";
import { api } from "./client";

export async function fetchAgents(): Promise<AgentDefinition[]> {
  const { data } = await api.get<AgentDefinition[]>("/agents");
  return data;
}

export async function fetchAgent(type: string): Promise<AgentDefinition> {
  const { data } = await api.get<AgentDefinition>(`/agents/${type}`);
  return data;
}

export async function createAgent(agent: AgentCreateRequest): Promise<AgentDefinition> {
  const { data } = await api.post<AgentDefinition>("/agents", agent);
  return data;
}

export async function updateAgent(type: string, agent: AgentUpdateRequest): Promise<AgentDefinition> {
  const { data } = await api.put<AgentDefinition>(`/agents/${type}`, agent);
  return data;
}

export async function deleteAgent(type: string): Promise<void> {
  await api.delete(`/agents/${type}`);
}

export async function resetAgent(type: string): Promise<AgentDefinition> {
  const { data } = await api.post<AgentDefinition>(`/agents/${type}/reset`);
  return data;
}

/** @deprecated Use fetchAgents() instead */
export async function fetchAgentTypes(): Promise<AgentDefinition[]> {
  return fetchAgents();
}
