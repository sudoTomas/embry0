import { api } from "./client";
import type { StatsResponse } from "@/lib/types";

export async function fetchStats(): Promise<StatsResponse> {
  const { data } = await api.get("/stats");
  return data;
}
