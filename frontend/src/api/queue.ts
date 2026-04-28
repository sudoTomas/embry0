import { api } from "./client";
import type { QueueResponse } from "@/lib/types";

export async function fetchQueue(): Promise<QueueResponse> {
  const { data } = await api.get<QueueResponse>("/queue");
  return data;
}
