import { Link, useParams } from "react-router";
import { useQaRunsForRepo } from "@/hooks/useQaDashboard";
import { QaRunRow } from "@/components/qa/QaRunRow";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { EmptyState } from "@/components/ui/EmptyState";

export function QaRepoDetailPage() {
  const { repo } = useParams<{ repo: string }>();
  const { data, isLoading, isError, refetch } = useQaRunsForRepo(repo, { limit: 50 });

  if (isError) return <PageError message="Failed to load runs for this repo" onRetry={() => refetch()} />;
  if (!repo || isLoading || !data) return <TableSkeleton />;

  const passing = data.filter((r) => r.overall_status === "passed").length;
  const failing = data.length - passing;

  return (
    <div className="space-y-4 p-6">
      <Link to="/qa/repos" className="text-sm text-white/50 hover:underline">
        ← all repos
      </Link>
      <h1 className="text-2xl font-bold">{repo}</h1>
      <p className="text-sm text-white/60">
        Last {data.length} runs · {passing} passed · {failing} failed
        {" · "}
        <Link
          to={`/qa/repos/${encodeURIComponent(repo)}/cache`}
          className="text-cyan-300 hover:underline"
          data-testid="cache-analytics-link"
        >
          Cache analytics
        </Link>
        {" · "}
        <Link
          to={`/qa/repos/${encodeURIComponent(repo)}/flake`}
          className="text-cyan-300 hover:underline"
          data-testid="flake-heatmap-link"
        >
          Flake heatmap
        </Link>
      </p>
      {data.length === 0 ? (
        <EmptyState
          stage="qa"
          title="No QA runs for this repo"
          description="Once a QA run completes against this repo, it will appear here."
        />
      ) : (
        <div className="space-y-2">
          {data.map((run) => (
            <QaRunRow key={run.job_id} run={run} />
          ))}
        </div>
      )}
    </div>
  );
}
