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
 * Absolute API URL for an artifact's bytes (includes the `/api/v1` prefix).
 *
 * NOTE: this URL is NOT routed through axios, so the configured Bearer token
 * is NOT attached. Inline-display consumers (e.g. `<img src>`, browser
 * `fetch`) MUST instead go through axios — use `artifactPath(...)` together
 * with `api.get(...)`, or the `useArtifactBlobUrl` hook for `<img>`. This
 * helper remains useful for direct-download UI (e.g. a future cookie-auth or
 * proxy-mode deploy where a top-level navigation does carry credentials).
 */
export function artifactUrl(
  runId: string,
  app: string,
  kind: ArtifactKind,
  filename: string,
): string {
  return `/api/v1/qa/runs/${encodeURIComponent(runId)}/apps/${encodeURIComponent(app)}/artifacts/${kind}/${encodeURIComponent(filename)}`;
}

/**
 * Same artifact path WITHOUT the `/api/v1` prefix — for axios calls.
 *
 * The `api` axios client is created with `baseURL: "/api/v1"`, so paths
 * passed to `api.get(...)` must be prefix-relative. Use this when fetching
 * artifact bytes through axios so the Bearer auth header is attached.
 */
export function artifactPath(
  runId: string,
  app: string,
  kind: ArtifactKind,
  filename: string,
): string {
  return `/qa/runs/${encodeURIComponent(runId)}/apps/${encodeURIComponent(app)}/artifacts/${kind}/${encodeURIComponent(filename)}`;
}
