import { api } from "./client";
import type { AgentTypeInfo } from "@/lib/types";

export async function fetchAgentTypes(): Promise<AgentTypeInfo[]> {
  const { data } = await api.get<AgentTypeInfo[]>("/agents/types");
  return data;
}
