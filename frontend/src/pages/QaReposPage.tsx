import { useQaRepos } from "@/hooks/useQaDashboard";
import { QaRepoCard } from "@/components/qa/QaRepoCard";
import { PageError } from "@/components/PageError";
import { DashboardSkeleton } from "@/components/ui/PageSkeleton";
import { EmptyState } from "@/components/ui/EmptyState";

export function QaReposPage() {
  const { data, isLoading, isError, refetch } = useQaRepos();

  if (isError) return <PageError message="Failed to load QA repos" onRetry={() => refetch()} />;
  if (isLoading || !data) return <DashboardSkeleton />;

  return (
    <div className="space-y-4 p-6">
      <h1 className="text-2xl font-bold">QA</h1>
      {data.length === 0 ? (
        <EmptyState
          stage="qa"
          title="No QA runs yet"
          description="Trigger a QA pipeline run on a repo with .embry0/qa.yaml v2 to see results here."
        />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {data.map((r) => (
            <QaRepoCard key={r.repo} repo={r} />
          ))}
        </div>
      )}
    </div>
  );
}
