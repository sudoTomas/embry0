/**
 * Phase 5B: dashboard tile that renders all screenshots captured during a
 * sub-task's exploratory testing as a thumbnail grid.
 *
 * Bytes flow through the orchestrator's `/qa/runs/.../artifacts/screenshots/...`
 * passthrough so the browser never sees a presigned URL — same auth surface
 * as the rest of the dashboard. `<img src>` cannot route through axios, so
 * each thumbnail uses `useArtifactBlobUrl` to fetch the bytes via the
 * authenticated axios client and assigns the resulting blob URL to `<img>`.
 *
 * The wrapping `<a target="_blank">` was dropped: a top-level navigation
 * would not carry the Bearer header, so it would 401 in production. The
 * inline thumbnail is sufficient for MVP triage; a future lightbox can show
 * full-size by reusing the already-fetched blob.
 */
import type { JSX } from "react";
import { useAppArtifacts } from "@/hooks/useQaDashboard";
import { useArtifactBlobUrl } from "@/hooks/useArtifactBlobUrl";

interface Props {
  runId: string;
  app: string;
}

interface ScreenshotThumbProps {
  runId: string;
  app: string;
  filename: string;
}

function ScreenshotThumb({ runId, app, filename }: ScreenshotThumbProps): JSX.Element {
  const { url, loading, error } = useArtifactBlobUrl(
    runId,
    app,
    "screenshots",
    filename,
  );
  if (error) {
    return (
      <div
        data-testid="qa-artifact-thumb-error"
        data-filename={filename}
        className="flex h-32 items-center justify-center rounded border border-destructive/40 bg-black/40 px-2 text-xs text-destructive"
        title={`${filename}: ${error}`}
      >
        Failed
      </div>
    );
  }
  if (loading || !url) {
    return (
      <div
        data-testid="qa-artifact-thumb-loading"
        data-filename={filename}
        className="h-32 w-full animate-pulse rounded border border-white/10 bg-white/5"
        title={filename}
      />
    );
  }
  return (
    <img
      data-testid="qa-artifact-thumb"
      data-filename={filename}
      src={url}
      alt={filename}
      loading="lazy"
      title={filename}
      className="h-32 w-full rounded border border-white/10 bg-black/40 object-cover"
    />
  );
}

export function QaArtifactGrid({ runId, app }: Props): JSX.Element {
  const { data, isLoading, isError } = useAppArtifacts(runId, app, "screenshots");

  if (isLoading) {
    return (
      <div className="text-sm text-white/40" data-testid="qa-artifact-grid-loading">
        Loading screenshots…
      </div>
    );
  }
  if (isError) {
    return (
      <div className="text-sm text-destructive" data-testid="qa-artifact-grid-error">
        Failed to load screenshots.
      </div>
    );
  }
  const filenames = data ?? [];
  if (filenames.length === 0) {
    return (
      <div className="text-sm text-white/40" data-testid="qa-artifact-grid-empty">
        No screenshots captured.
      </div>
    );
  }

  return (
    <div
      data-testid="qa-artifact-grid"
      className="grid grid-cols-2 gap-2 md:grid-cols-3 lg:grid-cols-4"
    >
      {filenames.map((fn) => (
        <ScreenshotThumb key={fn} runId={runId} app={app} filename={fn} />
      ))}
    </div>
  );
}
