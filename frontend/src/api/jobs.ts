import { api } from "./client";
import type { JobCreateRequest, JobFilters, JobListResponse, JobResponse } from "@/lib/types";

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

export async function discardJob(jobId: string): Promise<{ job_id: string; status: string; choice: string }> {
  const { data } = await api.post(`/jobs/${jobId}/resume`, { choice: "abandon" });
  return data;
}
