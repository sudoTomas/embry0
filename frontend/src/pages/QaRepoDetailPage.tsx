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
    <div className="space-y-4">
      <Link to="/qa/repos" className="text-sm text-white/50 hover:underline">
        ← all repos
      </Link>
      <h1 className="title-pixel text-xl font-bold">{repo}</h1>
      <p className="text-sm text-white/60">
        Last {data.length} runs · {passing} passed · {failing} failed
        {" · "}
        <Link
          to={`/qa/repos/${encodeURIComponent(repo)}/cache`}
          className="text-fg hover:text-primary hover:underline"
          data-testid="cache-analytics-link"
        >
          Cache analytics
        </Link>
        {" · "}
        <Link
          to={`/qa/repos/${encodeURIComponent(repo)}/flake`}
          className="text-fg hover:text-primary hover:underline"
          data-testid="flake-heatmap-link"
        >
          Flake heatmap
        </Link>
        {" · "}
        <Link
          to={`/qa/repos/${encodeURIComponent(repo)}/config`}
          className="text-fg hover:text-primary hover:underline"
          data-testid="qa-config-link"
        >
          QA config
        </Link>
      </p>
      {data.length === 0 ? (
        <EmptyState
          stage="qa"
          title="No QA runs for this repo"
          description="Once a QA run completes against this repo, it will appear here."
        />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Status</th>
                <th scope="col" className="num hidden sm:table-cell">Apps</th>
                <th scope="col" className="num">Started</th>
              </tr>
            </thead>
            <tbody>
              {data.map((run) => (
                <QaRunRow key={run.job_id} run={run} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
