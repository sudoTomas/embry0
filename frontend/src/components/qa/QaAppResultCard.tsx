import { Link } from "react-router";
import { Card, CardContent } from "@/components/ui/Card";
import { BootPhasePanel } from "./BootPhasePanel";
import { CacheHitsRow } from "./CacheHitsRow";
import { QaArtifactGrid } from "./QaArtifactGrid";
import { QaConsoleLogPanel } from "./QaConsoleLogPanel";
import { QaNetworkFailuresPanel } from "./QaNetworkFailuresPanel";
import { RunStatusBadge } from "./RunStatusBadge";
import type { AppResult } from "@/lib/types";

interface Props {
  app: AppResult;
  repo: string;
  /**
   * Run id (parent job id) the app result belongs to. Required to build the
   * `<run_id>__<app>/...` artifact path. Optional for back-compat with the
   * existing test fixtures and any caller that doesn't yet have a run id —
   * when undefined, the artifact section is hidden so the card still renders.
   */
  runId?: string;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const totalSeconds = Math.floor(ms / 1000);
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

const FAILED_STATUSES: ReadonlySet<string> = new Set([
  "qa_failure",
  "e2e_failure",
  "boot_failure",
  "ready_check_failed",
  "inconclusive",
  "infra_failure",
]);

export function QaAppResultCard({ app, repo, runId }: Props) {
  // Default the artifact <details> open for failing apps so users land on the
  // evidence without an extra click; collapse for passing ones to keep the
  // page compact when most apps are green.
  const artifactsDefaultOpen = FAILED_STATUSES.has(app.status);
  return (
    <Card data-testid="qa-app-card" data-app-name={app.app_name}>
      <CardContent className="p-4 space-y-2">
        <div className="flex items-center justify-between">
          <Link
            to={`/qa/repos/${encodeURIComponent(repo)}/apps/${encodeURIComponent(app.app_name)}`}
            className="font-semibold text-white/90 hover:underline"
          >
            {app.app_name}
          </Link>
          <RunStatusBadge status={app.status} />
        </div>
        <div className="flex items-center gap-3 text-sm text-white/60">
          <span>{formatDuration(app.duration_ms)}</span>
          <CacheHitsRow hits={app.cache_hits} />
          {app.trace_url && (
            <a
              href={app.trace_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-cyan-300 hover:underline"
            >
              trace
            </a>
          )}
        </div>
        {app.failure_summary && (
          <p
            className="rounded-sm border border-destructive/25 bg-destructive/10 px-2 py-1 text-sm text-destructive"
            role="alert"
          >
            {app.failure_summary}
          </p>
        )}
        {(app.status === "boot_failure" || app.status === "ready_check_failed") && (
          <BootPhasePanel boot_phase={app.boot_phase} />
        )}
        {runId && (
          <details
            data-testid="qa-app-card-artifacts"
            data-default-open={artifactsDefaultOpen}
            open={artifactsDefaultOpen}
            className="rounded-sm border border-white/10 bg-white/5 px-3 py-2"
          >
            <summary className="cursor-pointer text-xs uppercase tracking-wide text-white/50">
              Artifacts
            </summary>
            <div className="mt-3 space-y-3">
              <section>
                <header className="mb-1 text-xs uppercase tracking-wide text-white/40">
                  Screenshots
                </header>
                <QaArtifactGrid runId={runId} app={app.app_name} />
              </section>
              <section>
                <header className="mb-1 text-xs uppercase tracking-wide text-white/40">
                  Console
                </header>
                <QaConsoleLogPanel runId={runId} app={app.app_name} />
              </section>
              <section>
                <header className="mb-1 text-xs uppercase tracking-wide text-white/40">
                  Network failures
                </header>
                <QaNetworkFailuresPanel runId={runId} app={app.app_name} />
              </section>
            </div>
          </details>
        )}
      </CardContent>
    </Card>
  );
}
