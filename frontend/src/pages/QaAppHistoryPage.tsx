import { Link, useParams } from "react-router";
import { useQaAppHistory } from "@/hooks/useQaDashboard";
import { RunStatusBadge } from "@/components/qa/RunStatusBadge";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { EmptyState } from "@/components/ui/EmptyState";

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
    <div className="space-y-4 p-6">
      <Link
        to={`/qa/repos/${encodeURIComponent(repo)}`}
        className="text-sm text-white/50 hover:underline"
      >
        ← {repo}
      </Link>
      <h1 className="text-2xl font-bold">{app}</h1>
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
        <div className="overflow-hidden rounded-md border bg-card">
          <table className="w-full text-sm">
            <thead className="bg-card text-left text-xs uppercase text-white/40">
              <tr>
                <th className="px-4 py-2">Run</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2">Duration</th>
                <th className="px-4 py-2">When</th>
                <th className="px-4 py-2">Failure</th>
              </tr>
            </thead>
            <tbody>
              {data.map((h) => (
                <tr key={h.job_id} className="border-t border-white/5">
                  <td className="px-4 py-2 font-mono">
                    <Link
                      to={`/qa/runs/${encodeURIComponent(h.job_id)}`}
                      className="text-cyan-300 hover:underline"
                    >
                      {h.job_id}
                    </Link>
                  </td>
                  <td className="px-4 py-2">
                    <RunStatusBadge status={h.status} />
                  </td>
                  <td className="px-4 py-2 text-white/70">{formatDuration(h.duration_ms)}</td>
                  <td className="px-4 py-2 text-white/40">
                    {new Date(h.started_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-destructive">
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
