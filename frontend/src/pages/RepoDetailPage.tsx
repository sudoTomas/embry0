import { Link, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { FileCode2, GitPullRequest, KeyRound, ListChecks, Settings2, ShieldAlert } from "lucide-react";
import { fetchRepoDetail } from "@/api/repos";
import { useRepoEnv } from "@/hooks/useEnvironments";
import { useQaRunsForRepo } from "@/hooks/useQaDashboard";
import { QaConfigEditor } from "@/components/qa/QaConfigEditor";
import { RepoAccountsSection } from "@/components/repos/RepoAccountsSection";
import { RepoDockerAccessSection } from "@/components/repos/RepoDockerAccessSection";
import { RepoPreferencesSection } from "@/components/environments/RepoPreferencesSection";
import { PullRequestReviewCard } from "@/components/environments/PullRequestReviewCard";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";

/** RAV-1253: the unified per-repo view — registry state, QA config editor,
 * environment (masked), forge account, sandbox preferences, and recent QA runs. */
export function RepoDetailPage() {
  // Splat param: the repo path can be `owner/name` OR a GitLab nested
  // subgroup like `group/subgroup/project` (#162).
  const params = useParams();
  const full = params["*"] ?? "";
  const validRepo = full.split("/").filter(Boolean).length >= 2;

  const detail = useQuery({
    queryKey: ["repos", "detail", full],
    queryFn: () => fetchRepoDetail(full),
    enabled: validRepo,
    retry: false,
  });
  const { data: envVars = [] } = useRepoEnv(full);
  const runs = useQaRunsForRepo(validRepo ? full : undefined, { limit: 10 });

  if (!validRepo) return <PageError message="Invalid repo — expected a path like owner/name" />;
  if (detail.isError)
    return (
      <PageError
        message={`${full} is not registered in embry0`}
        onRetry={() => void detail.refetch()}
      />
    );
  if (detail.isLoading || !detail.data) return <TableSkeleton />;

  const repo = detail.data;

  return (
    <div className="space-y-6 p-6">
      <div>
        <Link to="/qa/repos" className="text-sm text-white/50 hover:underline">
          ← Repos
        </Link>
        <div className="mt-1 flex items-center gap-3">
          <h1 className="title-pixel text-2xl font-bold">{full}</h1>
          <Badge tone="neutral" data-testid="repo-detail-forge">
            {repo.forge === "gitlab" ? "GitLab" : "GitHub"}
          </Badge>
          {repo.active ? <Badge tone="success">active</Badge> : <Badge tone="neutral">inactive</Badge>}
          {repo.has_qa_config ? (
            <Badge tone="success">QA config stored</Badge>
          ) : (
            <Badge tone="warning">no QA config</Badge>
          )}
        </div>
        {repo.notes && <p className="mt-1 text-sm text-white/60">{repo.notes}</p>}
        {repo.qa_config_updated_at && (
          <p className="mt-1 text-xs text-muted-foreground" data-testid="repo-config-meta">
            QA config updated {repo.qa_config_updated_at}
            {repo.qa_config_updated_by ? ` by ${repo.qa_config_updated_by}` : ""}
          </p>
        )}
      </div>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <FileCode2 className="h-4 w-4 text-white/50" />
          <h2 className="text-lg font-semibold">QA config</h2>
        </div>
        <QaConfigEditor repo={full} />
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-white/50" />
          <h2 className="text-lg font-semibold">Environment</h2>
          <Link
            to={`/environments?repo=${encodeURIComponent(full)}`}
            className="ml-auto text-sm text-cyan-300 hover:underline"
            data-testid="repo-env-manage-link"
          >
            Manage in Environments →
          </Link>
        </div>
        {envVars.length === 0 ? (
          <p className="text-sm text-muted-foreground">No repo-scoped environment variables.</p>
        ) : (
          <Card className="divide-y divide-white/5 p-0">
            {envVars.map((v) => (
              <div
                key={v.key}
                className="flex items-center gap-3 px-4 py-2 text-sm"
                data-testid="repo-env-row"
              >
                <code className="font-mono">{v.key}</code>
                <Badge tone={v.var_type === "secret" ? "warning" : "neutral"}>{v.var_type}</Badge>
                {v.scope === "qa" && <Badge tone="info">qa</Badge>}
                <span className="ml-auto font-mono text-white/50">
                  {v.var_type === "secret" ? "****" : v.value}
                </span>
              </div>
            ))}
          </Card>
        )}
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-white/50" />
          <h2 className="text-lg font-semibold">Accounts</h2>
        </div>
        <RepoAccountsSection repo={repo.repo} forge={repo.forge} />
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <Settings2 className="h-4 w-4 text-white/50" />
          <h2 className="text-lg font-semibold">Sandbox preferences</h2>
        </div>
        <RepoPreferencesSection repo={full} />
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <GitPullRequest className="h-4 w-4 text-white/50" />
          <h2 className="text-lg font-semibold">Pull request review</h2>
        </div>
        <PullRequestReviewCard repo={full} />
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-white/50" />
          <h2 className="text-lg font-semibold">Docker access</h2>
        </div>
        <RepoDockerAccessSection repo={repo.repo} dockerAccess={repo.docker_access === true} />
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <ListChecks className="h-4 w-4 text-white/50" />
          <h2 className="text-lg font-semibold">Recent QA runs</h2>
        </div>
        {(runs.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No QA runs yet.</p>
        ) : (
          <Card className="divide-y divide-white/5 p-0">
            {(runs.data ?? []).map((run) => (
              <Link
                key={run.job_id}
                to={`/qa/runs/${run.job_id}`}
                className="flex items-center gap-3 px-4 py-2 text-sm hover:bg-white/[0.03]"
                data-testid="repo-run-row"
              >
                <code className="font-mono text-white/70">{run.job_id}</code>
                <Badge
                  tone={
                    run.overall_status === "passed"
                      ? "success"
                      : run.overall_status === "failed"
                        ? "error"
                        : "warning"
                  }
                >
                  {run.overall_status}
                </Badge>
                <span className="ml-auto text-white/50">{run.started_at}</span>
              </Link>
            ))}
          </Card>
        )}
      </section>
    </div>
  );
}
