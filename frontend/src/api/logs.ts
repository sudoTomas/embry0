import { api } from "./client";
import type { LogEvent } from "@/lib/types/events";

export async function fetchJobLogEvents(jobId: string): Promise<LogEvent[]> {
  const { data } = await api.get(`/jobs/${jobId}/logs/events`);
  return data.events;
}
