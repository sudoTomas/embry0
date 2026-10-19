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
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <h1 className="title-pixel text-2xl font-bold">QA</h1>
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
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {data.map((r) => (
            <QaRepoCard key={r.repo} repo={r} />
          ))}
        </div>
      )}
    </div>
  );
}
