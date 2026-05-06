import { api } from "./client";
import type {
  AppHistoryItem,
  AppResult,
  RepoEntry,
  RunDetail,
  RunListItem,
} from "@/lib/types";

export async function fetchQaRepos(limit = 50): Promise<RepoEntry[]> {
  const { data } = await api.get<RepoEntry[]>("/qa/repos", {
    params: { limit },
  });
  return data;
}

export async function fetchQaRunsForRepo(
  repo: string,
  opts: { limit?: number; offset?: number } = {},
): Promise<RunListItem[]> {
  const { data } = await api.get<RunListItem[]>(
    `/qa/repos/${encodeURIComponent(repo)}/runs`,
    { params: { limit: opts.limit ?? 50, offset: opts.offset ?? 0 } },
  );
  return data;
}

export async function fetchQaAppHistory(
  repo: string,
  app: string,
  limit = 20,
): Promise<AppHistoryItem[]> {
  const { data } = await api.get<AppHistoryItem[]>(
    `/qa/repos/${encodeURIComponent(repo)}/apps/${encodeURIComponent(app)}/history`,
    { params: { limit } },
  );
  return data;
}

export async function fetchQaRun(runId: string): Promise<RunDetail> {
  const { data } = await api.get<RunDetail>(
    `/qa/runs/${encodeURIComponent(runId)}`,
  );
  return data;
}

export async function fetchQaRunApp(
  runId: string,
  app: string,
): Promise<AppResult> {
  const { data } = await api.get<AppResult>(
    `/qa/runs/${encodeURIComponent(runId)}/apps/${encodeURIComponent(app)}`,
  );
  return data;
}

// ─── Per-sub-task artifact passthrough (Phase 5B) ─────────────────────────
//
// The orchestrator proxies bytes from MinIO under the dashboard auth, so the
// browser never sees a presigned URL. The `kind` is allow-listed server-side;
// duplicate it here as a string-literal union so callers get a compile-time
// check too.

export type ArtifactKind = "screenshots" | "network" | "console" | "traces";

export async function listAppArtifacts(
  runId: string,
  app: string,
  kind: ArtifactKind,
): Promise<string[]> {
  const { data } = await api.get<{ filenames: string[] }>(
    `/qa/runs/${encodeURIComponent(runId)}/apps/${encodeURIComponent(app)}/artifacts/${kind}`,
  );
  return data.filenames;
}

/**
 * Build the URL the browser hits to fetch a single artifact's bytes.
 *
 * Both endpoints sit under the dashboard auth — the browser will send the
 * configured Bearer token via the axios default header for XHR requests, and
 * sets `?_=<token>` is intentionally NOT used here (we rely on the same auth
 * surface as every other dashboard call). Components that pass this URL
 * directly to `<img src>` rely on the same-origin cookie / static auth.
 */
export function artifactUrl(
  runId: string,
  app: string,
  kind: ArtifactKind,
  filename: string,
): string {
  return `/api/v1/qa/runs/${encodeURIComponent(runId)}/apps/${encodeURIComponent(app)}/artifacts/${kind}/${encodeURIComponent(filename)}`;
}
