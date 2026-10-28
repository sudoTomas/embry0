import { Link, useParams } from "react-router";
import { useQaAppHistory } from "@/hooks/useQaDashboard";
import { RunStatusBadge } from "@/components/qa/RunStatusBadge";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { formatDateTime } from "@/lib/utils";

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  const totalSeconds = Math.floor(ms / 1000);
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  if (m === 0) return `${s}s`;
  return `${m}m ${s}s`;
}

export function QaAppHistoryPage() {
  const { repo, app } = useParams<{ repo: string; app: string }>();
  const { data, isLoading, isError, refetch } = useQaAppHistory(repo, app);

  if (isError) return <PageError message="Failed to load app history" onRetry={() => refetch()} />;
  if (!repo || !app || isLoading || !data) return <TableSkeleton />;

  const passing = data.filter((h) => h.status === "passed").length;

  return (
    <div className="space-y-4">
      <Link
        to={`/qa/repos/${encodeURIComponent(repo)}`}
        className="text-sm text-white/50 hover:underline"
      >
        ← {repo}
      </Link>
      <h1 className="title-page text-xl font-bold">{app}</h1>
      <p className="text-sm text-white/60">
        Last {data.length} runs · {passing} passed · {data.length - passing} failed
      </p>
      {data.length === 0 ? (
        <EmptyState
          stage="qa"
          title="No history yet"
          description="Once a QA run includes this app, it will appear here."
        />
      ) : (
        // Like the repo's run history: the run id is the row's only link and
        // never wraps; below sm duration and failure step aside so the id,
        // the status and the time fit a phone.
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Status</th>
                <th scope="col" className="hidden sm:table-cell">Duration</th>
                <th scope="col">When</th>
                <th scope="col" className="hidden sm:table-cell">Failure</th>
              </tr>
            </thead>
            <tbody>
              {data.map((h) => (
                <tr key={h.job_id}>
                  <td className="whitespace-nowrap">
                    <Link
                      to={`/qa/runs/${encodeURIComponent(h.job_id)}`}
                      className="font-mono text-xs text-fg hover:text-primary hover:underline"
                    >
                      {h.job_id}
                    </Link>
                  </td>
                  <td>
                    <RunStatusBadge status={h.status} />
                  </td>
                  <td className="hidden text-white/70 sm:table-cell">{formatDuration(h.duration_ms)}</td>
                  <td className="whitespace-nowrap text-fg-muted">
                    {formatDateTime(h.started_at)}
                  </td>
                  <td className="hidden text-destructive sm:table-cell">
                    {h.failure_summary || ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
