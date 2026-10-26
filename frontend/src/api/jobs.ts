import { api } from "./client";
import type { AgentSession, JobCreateRequest, JobFilters, JobListResponse, JobResponse, JobVerification } from "@/lib/types";
import type { DoneWhen, DoneWhenEdit } from "@/lib/types/jobs";

export async function fetchJobs(filters: JobFilters = {}): Promise<JobListResponse> {
  // Lists show one line per job, so ask for brief tasks: a watcher analysis
  // job's task is its whole log briefing, and a page of them weighed close
  // to a megabyte. The detail page fetches the full text by id.
  const { data } = await api.get("/jobs", { params: { ...filters, brief: true } });
  return data;
}

export async function fetchJob(jobId: string): Promise<JobResponse> {
  const { data } = await api.get(`/jobs/${jobId}`);
  return data;
}

export async function createJob(req: JobCreateRequest): Promise<JobResponse> {
  const { data } = await api.post("/jobs", req);
  return data;
}

export async function cancelJob(jobId: string): Promise<JobResponse> {
  const { data } = await api.post(`/jobs/${jobId}/cancel`);
  return data;
}

/** Short-lived job-scoped WS token (RAV-1070) — the only credential that
 * rides the WebSocket subprotocol; the raw API key stays in headers. */
export async function fetchJobEventsToken(jobId: string): Promise<{ token: string; expires_in: number }> {
  const { data } = await api.post(`/jobs/${jobId}/events-token`);
  return data;
}

export async function resumeJob(jobId: string, choice: string, guidance?: string): Promise<{ job_id: string; status: string; choice: string }> {
  const { data } = await api.post(`/jobs/${jobId}/resume`, { choice, guidance });
  return data;
}

/** Replace a job's finish line (done when); the next coding run works to it. */
export async function updateDoneWhen(jobId: string, body: DoneWhenEdit): Promise<DoneWhen> {
  const { data } = await api.put(`/jobs/${jobId}/done-when`, body);
  return data;
}

export async function discardJob(jobId: string): Promise<{ job_id: string; status: string; choice: string }> {
  const { data } = await api.post(`/jobs/${jobId}/resume`, { choice: "abandon" });
  return data;
}

/** The HTTP status of a failed request, undefined when the error carried none. */
export function statusOf(err: unknown): number | undefined {
  return (err as { response?: { status?: number } } | undefined)?.response?.status;
}

/** The job's verification row; null when the job has none (404). */
export async function fetchJobVerification(jobId: string): Promise<JobVerification | null> {
  try {
    const { data } = await api.get(`/jobs/${jobId}/verification`);
    return data;
  } catch (err) {
    if (statusOf(err) === 404) return null;
    throw err;
  }
}

/** One agent's stored transcript; null when there is none (404) or only a CLI blob (204). */
export async function fetchAgentSession(jobId: string, agentType: string): Promise<AgentSession | null> {
  try {
    const res = await api.get(`/jobs/${jobId}/agents/${encodeURIComponent(agentType)}/session`);
    if (res.status === 204 || !res.data) return null;
    return res.data;
  } catch (err) {
    if (statusOf(err) === 404) return null;
    throw err;
  }
}
