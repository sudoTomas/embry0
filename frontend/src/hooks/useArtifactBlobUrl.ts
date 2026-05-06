/**
 * Fetch artifact bytes via the authenticated axios client and expose them as
 * a blob URL the browser can put in `<img src>`.
 *
 * `<img src>` cannot route through axios, and a bare `<img src="/api/v1/...">`
 * does NOT carry the Bearer header configured on the axios default. This hook
 * bridges the two: it fetches the bytes via `api.get(..., { responseType:
 * "blob" })` (so the Bearer header is attached), creates an object URL, and
 * returns it. The URL is revoked on cleanup (unmount or input change) so we
 * don't leak Blob storage.
 */
import { useEffect, useState } from "react";
import { api } from "@/api/client";
import type { ArtifactKind } from "@/api/qaDashboard";
import { artifactPath } from "@/api/qaDashboard";

export interface ArtifactBlobUrl {
  url: string | null;
  loading: boolean;
  error: string | null;
}

export function useArtifactBlobUrl(
  runId: string,
  app: string,
  kind: ArtifactKind,
  filename: string,
): ArtifactBlobUrl {
  const [url, setUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let createdUrl: string | null = null;
    setLoading(true);
    setError(null);
    setUrl(null);
    api
      .get<Blob>(artifactPath(runId, app, kind, filename), {
        responseType: "blob",
      })
      .then((res) => {
        if (cancelled) return;
        createdUrl = URL.createObjectURL(res.data);
        setUrl(createdUrl);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      if (createdUrl) URL.revokeObjectURL(createdUrl);
    };
  }, [runId, app, kind, filename]);

  return { url, loading, error };
}
