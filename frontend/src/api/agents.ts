import { api } from "./client";
import type { AgentTypeInfo } from "@/lib/types";

export async function fetchAgentTypes(): Promise<AgentTypeInfo[]> {
  const { data } = await api.get("/agents/types");
  return data.agents;
}
