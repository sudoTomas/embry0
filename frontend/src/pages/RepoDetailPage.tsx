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
import { isNotFound } from "@/api/errorDetail";
import { TableSkeleton } from "@/components/TableSkeleton";
import { Badge } from "@/components/ui/Badge";
import { RunStatusBadge } from "@/components/qa/RunStatusBadge";
import { statusLabel } from "@/lib/status";

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
  if (isNotFound(detail.error))
    return <PageError message={`${full} is not registered in embry0`} back={{ to: "/repos", label: "All repositories" }} />;
  if (detail.isError)
    return <PageError message={`Failed to load ${full}`} onRetry={() => void detail.refetch()} />;
  if (detail.isLoading || !detail.data) return <TableSkeleton />;

  const repo = detail.data;

  return (
    <div className="space-y-6">
      <div>
        <Link to="/qa/repos" className="text-sm text-white/50 hover:underline">
          ← Repos
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="text-xl font-bold">{full}</h1>
          <Badge tone="neutral" data-testid="repo-detail-forge">
            {repo.forge === "gitlab" ? "GitLab" : "GitHub"}
          </Badge>
          {repo.active ? <Badge tone="success">Active</Badge> : <Badge tone="neutral">Inactive</Badge>}
          {repo.has_qa_config ? (
            <Badge tone="success">QA config stored</Badge>
          ) : (
            <Badge tone="warning">No QA config</Badge>
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
          <FileCode2 className="h-4 w-4 text-fg-muted" />
          <h2 className="text-section font-semibold">QA config</h2>
        </div>
        <QaConfigEditor repo={full} />
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-fg-muted" />
          <h2 className="text-section font-semibold">Environment</h2>
          <Link
            to={`/environments?repo=${encodeURIComponent(full)}`}
            className="ml-auto text-sm text-primary hover:underline"
            data-testid="repo-env-manage-link"
          >
            Manage in Environments →
          </Link>
        </div>
        {envVars.length === 0 ? (
          <p className="text-sm text-muted-foreground">No repo-scoped environment variables.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Key</th>
                  <th scope="col">Type</th>
                  <th scope="col">Value</th>
                </tr>
              </thead>
              <tbody>
                {envVars.map((v) => (
                  <tr key={v.key} data-testid="repo-env-row">
                    <td className="font-mono text-xs">{v.key}</td>
                    <td className="whitespace-nowrap">
                      <span className="inline-flex items-center gap-1.5">
                        <Badge tone={v.var_type === "secret" ? "warning" : "neutral"}>{statusLabel(v.var_type)}</Badge>
                        {v.scope === "qa" && <Badge tone="neutral">QA</Badge>}
                      </span>
                    </td>
                    <td className="w-full max-w-0 truncate font-mono text-xs text-fg-muted">
                      {v.var_type === "secret" ? "****" : v.value}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <KeyRound className="h-4 w-4 text-fg-muted" />
          <h2 className="text-section font-semibold">Accounts</h2>
        </div>
        <RepoAccountsSection repo={repo.repo} forge={repo.forge} />
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <Settings2 className="h-4 w-4 text-fg-muted" />
          <h2 className="text-section font-semibold">Sandbox preferences</h2>
        </div>
        <RepoPreferencesSection repo={full} />
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <GitPullRequest className="h-4 w-4 text-fg-muted" />
          <h2 className="text-section font-semibold">Pull request review</h2>
        </div>
        <PullRequestReviewCard repo={full} />
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-fg-muted" />
          <h2 className="text-section font-semibold">Docker access</h2>
        </div>
        <RepoDockerAccessSection repo={repo.repo} dockerAccess={repo.docker_access === true} />
      </section>

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <ListChecks className="h-4 w-4 text-fg-muted" />
          <h2 className="text-section font-semibold">Recent QA runs</h2>
        </div>
        {(runs.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No QA runs yet.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Run</th>
                  <th scope="col">Status</th>
                  <th scope="col" className="num">Started</th>
                </tr>
              </thead>
              <tbody>
                {(runs.data ?? []).map((run) => (
                  <tr key={run.job_id}>
                    <td className="w-full max-w-0 truncate">
                      <Link
                        to={`/qa/runs/${run.job_id}`}
                        className="font-mono text-xs text-fg hover:text-primary hover:underline"
                        data-testid="repo-run-row"
                      >
                        {run.job_id}
                      </Link>
                    </td>
                    <td className="w-px">
                      <RunStatusBadge status={run.overall_status} />
                    </td>
                    <td className="num whitespace-nowrap text-fg-muted">
                      {new Date(run.started_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
