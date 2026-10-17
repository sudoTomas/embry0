import { api } from "./client";
import type { LogEvent } from "@/lib/types/events";
import { normalizeWireEvent } from "@/lib/wire";
import type { LogsContainersResponse, LogsQueryParams, LogsQueryResponse } from "@/lib/types";

interface LogEventsPage {
  events: unknown[];
  next_since_seq?: number;
  has_more?: boolean;
}

/** The persisted event log from `sinceSeq` (exclusive) to the tail; the API pages it (#203). */
export async function fetchJobLogEvents(jobId: string, sinceSeq = 0): Promise<LogEvent[]> {
  const events: LogEvent[] = [];
  let cursor = sinceSeq;
  for (;;) {
    const { data } = await api.get<LogEventsPage>(`/jobs/${jobId}/logs/events`, {
      params: { since_seq: cursor, limit: 1000 },
    });
    events.push(...data.events.map(normalizeWireEvent));
    const next = data.next_since_seq;
    if (!data.has_more || typeof next !== "number" || next <= cursor) return events;
    cursor = next;
  }
}

/** RAV-1295: the set of containers Loki is collecting (for the dropdown). */
export async function fetchLogContainers(): Promise<LogsContainersResponse> {
  const { data } = await api.get<LogsContainersResponse>("/logs/containers");
  return data;
}

/** RAV-1057: on-demand container-log query via the orchestrator's Loki proxy. */
export async function queryLogs(params: LogsQueryParams): Promise<LogsQueryResponse> {
  const { data } = await api.get<LogsQueryResponse>("/logs/query", { params });
  return data;
}
