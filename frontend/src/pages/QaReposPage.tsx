import { Link } from "react-router";
import { FolderGit2 } from "lucide-react";
import { useQaRepos } from "@/hooks/useQaDashboard";
import { QaRepoCard } from "@/components/qa/QaRepoCard";
import { PageError } from "@/components/PageError";
import { DashboardSkeleton } from "@/components/ui/PageSkeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { buttonVariants } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

// RAV-1301: registration and the registry list moved to the central /repos
// page — this page is QA runs only.

export function QaReposPage() {
  const { data, isLoading, isError, refetch } = useQaRepos();

  if (isError) return <PageError message="Failed to load QA repos" onRetry={() => refetch()} />;
  if (isLoading || !data) return <DashboardSkeleton />;

  const reposCta = (
    <Link
      to="/repos"
      className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
      data-testid="add-repo-cta"
    >
      <FolderGit2 className="h-4 w-4" /> Add repo
    </Link>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="title-page text-xl font-bold">QA</h1>
        {reposCta}
      </div>
      {data.length === 0 ? (
        <EmptyState
          stage="qa"
          title="No QA runs yet"
          description="Register a repo and store its QA config in embry0, then trigger a QA pipeline run to see results here."
        >
          {reposCta}
        </EmptyState>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Repository</th>
                <th scope="col">Latest run</th>
                <th scope="col" className="num">Apps</th>
                <th scope="col" className="hidden sm:table-cell">Run</th>
              </tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <QaRepoCard key={r.repo} repo={r} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
