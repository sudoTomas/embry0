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

/** Download an artifact deliverable and hand it to the browser as a file.
 * The endpoint streams bytes through the orchestrator (a presigned MinIO URL
 * would point at the in-compose `minio:9000` host, unreachable from a LAN
 * browser), so we fetch a blob via the authenticated client and save it. */
export async function downloadDeliverable(deliverable: Deliverable): Promise<void> {
  const { data } = await api.get<Blob>(
    `/jobs/${deliverable.job_id}/deliverables/${deliverable.id}/download`,
    { responseType: "blob" },
  );
  const objectUrl = URL.createObjectURL(data);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = (deliverable.title || deliverable.storage_key || "artifact").split("/").pop() || "artifact";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}
