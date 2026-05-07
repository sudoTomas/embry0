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
 * Clicking a thumbnail opens an in-app lightbox at full size, reusing the
 * blob URL the thumbnail already fetched (no extra request, no auth issue).
 * Escape, click-outside, or the Close button dismisses.
 */
import { type JSX, useEffect, useState } from "react";
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

function ScreenshotLightbox({
  url,
  filename,
  onClose,
}: {
  url: string;
  filename: string;
  onClose: () => void;
}): JSX.Element {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Screenshot ${filename}`}
      data-testid="qa-artifact-lightbox"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4"
      onClick={onClose}
    >
      <div
        className="relative flex max-h-full max-w-full flex-col items-center gap-2"
        onClick={(e) => e.stopPropagation()}
      >
        <img
          data-testid="qa-artifact-lightbox-image"
          src={url}
          alt={filename}
          className="block max-h-[85vh] max-w-full rounded border border-white/10 object-contain"
        />
        <div className="flex w-full items-center justify-between gap-4 text-sm text-white/70">
          <div className="truncate font-mono" title={filename}>
            {filename}
          </div>
          <button
            type="button"
            onClick={onClose}
            data-testid="qa-artifact-lightbox-close"
            className="shrink-0 rounded border border-white/20 px-3 py-1 text-white/80 hover:bg-white/10"
            aria-label="Close screenshot"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function ScreenshotThumb({ runId, app, filename }: ScreenshotThumbProps): JSX.Element {
  const { url, loading, error } = useArtifactBlobUrl(
    runId,
    app,
    "screenshots",
    filename,
  );
  const [expanded, setExpanded] = useState(false);

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
    <>
      <button
        type="button"
        onClick={() => setExpanded(true)}
        aria-label={`Open ${filename} at full size`}
        data-testid="qa-artifact-thumb-button"
        data-filename={filename}
        className="block h-32 w-full cursor-zoom-in overflow-hidden rounded border border-white/10 bg-black/40 p-0 transition-colors hover:border-white/30"
      >
        <img
          data-testid="qa-artifact-thumb"
          data-filename={filename}
          src={url}
          alt={filename}
          loading="lazy"
          title={filename}
          className="h-full w-full object-cover"
        />
      </button>
      {expanded && (
        <ScreenshotLightbox
          url={url}
          filename={filename}
          onClose={() => setExpanded(false)}
        />
      )}
    </>
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
