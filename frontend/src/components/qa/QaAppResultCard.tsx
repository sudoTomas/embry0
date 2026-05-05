import { Link } from "react-router";
import { Card, CardContent } from "@/components/ui/Card";
import { CacheHitsRow } from "./CacheHitsRow";
import { RunStatusBadge } from "./RunStatusBadge";
import type { AppResult } from "@/lib/types";

interface Props {
  app: AppResult;
  repo: string;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const totalSeconds = Math.floor(ms / 1000);
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

export function QaAppResultCard({ app, repo }: Props) {
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
      </CardContent>
    </Card>
  );
}
