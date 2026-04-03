import { api } from "./client";
import type { ConfigResponse, ConfigUpdateRequest } from "@/lib/types";

export async function fetchConfig(): Promise<ConfigResponse> {
  const { data } = await api.get("/config");
  return data;
}

export async function updateConfig(req: ConfigUpdateRequest): Promise<ConfigResponse> {
  const { data } = await api.put("/config", req);
  return data;
}
