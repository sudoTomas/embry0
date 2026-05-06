/**
 * Phase 5B: dashboard tile that renders all screenshots captured during a
 * sub-task's exploratory testing as a thumbnail grid.
 *
 * Bytes flow through the orchestrator's `/qa/runs/.../artifacts/screenshots/...`
 * passthrough so the browser never sees a presigned URL — same auth surface as
 * the rest of the dashboard. Each thumbnail links to the full-size image which
 * opens in a new tab (the MVP triage path; lightbox is a future enhancement).
 */
import type { JSX } from "react";
import { useAppArtifacts } from "@/hooks/useQaDashboard";
import { artifactUrl } from "@/api/qaDashboard";

interface Props {
  runId: string;
  app: string;
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
      {filenames.map((fn) => {
        const href = artifactUrl(runId, app, "screenshots", fn);
        return (
          <a
            key={fn}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="block overflow-hidden rounded border border-white/10 bg-black/40 transition hover:border-white/30"
            title={fn}
          >
            <img
              src={href}
              alt={fn}
              loading="lazy"
              className="h-32 w-full object-cover"
            />
          </a>
        );
      })}
    </div>
  );
}
