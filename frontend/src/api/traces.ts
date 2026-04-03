import { api } from "./client";
import type { TraceFilters, TraceListResponse } from "@/lib/types";

export async function fetchTraces(filters: TraceFilters = {}): Promise<TraceListResponse> {
  const { data } = await api.get("/traces", { params: filters });
  return data;
}
