import { Link, useParams } from "react-router";
import { useQaRun } from "@/hooks/useQaDashboard";
import { useQaRunEvents } from "@/hooks/useQaRunEvents";
import { QaAppResultCard } from "@/components/qa/QaAppResultCard";
import { RunStatusBadge } from "@/components/qa/RunStatusBadge";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";

export function QaRunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const { data, isLoading, isError, refetch } = useQaRun(runId);
  // Phase 5C: subscribe to SSE for sub-second liveness on per-app status
  // changes. The hook invalidates the same query key useQaRun reads, so the
  // existing 15s polling stays as the fallback when the SSE connection
  // can't be established (e.g. production-Bearer auth blocks EventSource).
  useQaRunEvents(runId);

  if (isError) return <PageError message="Failed to load run detail" onRetry={() => refetch()} />;
  if (!runId || isLoading || !data) return <TableSkeleton />;

  return (
    <div className="space-y-4 p-6">
      <Link
        to={`/qa/repos/${encodeURIComponent(data.repo)}`}
        className="text-sm text-white/50 hover:underline"
      >
        ← {data.repo}
      </Link>
      <div className="flex items-baseline justify-between">
        <h1 className="text-2xl font-bold font-mono">{data.job_id}</h1>
        <RunStatusBadge status={data.overall_status} />
      </div>
      <p className="text-sm text-white/60">
        {data.apps.length} apps · started {new Date(data.started_at).toLocaleString()}
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {data.apps.map((a) => (
          <QaAppResultCard
            key={a.app_name}
            app={a}
            repo={data.repo}
            runId={data.job_id}
          />
        ))}
      </div>
    </div>
  );
}
