import { api } from "./client";

export type DeliverableType = "pr" | "report" | "artifact" | "message";

export interface Deliverable {
  id: string;
  job_id: string;
  type: DeliverableType;
  title: string;
  content: string | null;
  url: string | null;
  storage_bucket: string | null;
  storage_key: string | null;
  media_type: string | null;
  size_bytes: number | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export async function fetchDeliverables(jobId: string): Promise<Deliverable[]> {
  const { data } = await api.get<Deliverable[]>(`/jobs/${jobId}/deliverables`);
  return data;
}

/** Resolve an artifact deliverable to a short-lived presigned URL.
 * The endpoint's default 302 can't be followed as a plain link (the Bearer
 * header doesn't ride browser navigations), so we ask for JSON and open it. */
export async function fetchDeliverableDownloadUrl(jobId: string, deliverableId: string): Promise<string> {
  const { data } = await api.get<{ url: string }>(
    `/jobs/${jobId}/deliverables/${deliverableId}/download`,
    { params: { redirect: false } },
  );
  return data.url;
}
