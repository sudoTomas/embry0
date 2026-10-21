import { Link, useLocation, useParams } from "react-router";
import { useQueries, useQuery, type UseQueryOptions } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";
import { fetchRepoDetail } from "@/api/repos";
import { credentialsQuery, repoCredentialsQuery } from "@/hooks/useCredentials";
import { repoEnvQuery, useRepoEnv } from "@/hooks/useEnvironments";
import { hasAnswered, useFirstPaint } from "@/hooks/useFirstPaint";
import { modelsCatalogQuery } from "@/hooks/useProvider";
import { qaConfigQuery } from "@/hooks/useQaConfig";
import { useRepoReadiness } from "@/hooks/useRepoReadiness";
import { repoPreferencesQuery } from "@/hooks/useRepoPreferences";
import { useQaRunsForRepo } from "@/hooks/useQaDashboard";
import { QaConfigEditor } from "@/components/qa/QaConfigEditor";
import { RepoAccountsSection } from "@/components/repos/RepoAccountsSection";
import { RepoStandardsRow } from "@/components/standards/RepoStandardsRow";
import { RepoDockerAccessSection } from "@/components/repos/RepoDockerAccessSection";
import { ReadinessChip, SetupChecklist } from "@/components/repos/SetupChecklist";
import { AdvancedSection } from "@/components/repos/AdvancedSection";
import { HistoryPanel } from "@/components/repos/HistoryPanel";
import { RepoPreferencesSection } from "@/components/environments/RepoPreferencesSection";
import { PullRequestReviewCard } from "@/components/environments/PullRequestReviewCard";
import { PageError } from "@/components/PageError";
import { isNotFound } from "@/api/errorDetail";
import { TableSkeleton } from "@/components/TableSkeleton";
import { Badge } from "@/components/ui/Badge";
import { RunStatusBadge } from "@/components/qa/RunStatusBadge";
import { linkedSetting, type SettingKey } from "@/lib/settingGuide";
import { providerLabel, statusLabel } from "@/lib/status";
import { formatDateTime } from "@/lib/utils";

/**
 * The answers an Advanced section's body takes its height from. `name` is
 * the repository as its details name it, which the accounts body is given.
 * Docker access and Design checks are drawn from the page's own details (a
 * running audit's status badge aside), so they wait on nothing more.
 */
function bodyQueries(setting: SettingKey, full: string, name: string): UseQueryOptions[] {
  switch (setting) {
    case "testSetup":
      return [qaConfigQuery(full)];
    case "appSettings":
      return [repoEnvQuery(full)];
    case "workMachine":
    case "prReview":
      return [repoPreferencesQuery(full)];
    case "accounts":
      return [repoCredentialsQuery(name), credentialsQuery(), modelsCatalogQuery()];
    default:
      return [];
  }
}

/** RAV-1253: the unified per-repo view. It opens on the setup checklist. Every
 * technical control sits below it under Advanced settings, closed, each
 * section saying what it does, when it takes effect and how to undo it; then
 * the history of setting changes and recent test runs. */
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
  const readiness = useRepoReadiness(validRepo ? full : "");
  // An address that names an Advanced section (#advanced-accounts) opens the
  // page on it. Painted before that section's body had its answers, the body
  // grew as they landed and pushed everything under it down (a layout shift
  // of 0.2 at desktop width for Accounts, 0.37 on a phone). The page waits on
  // them as it does on the setup status, for at most FIRST_PAINT_WAIT_MS, and
  // fetches them here so they load beside the details.
  const linked = linkedSetting(useLocation().hash);
  const linkedAnswered = useQueries({
    queries: linked && validRepo ? bodyQueries(linked, full, detail.data?.repo ?? full) : [],
    combine: (results) => results.every(hasAnswered),
  });
  const linkedPainted = useFirstPaint(linkedAnswered);

  if (!validRepo) return <PageError message="Invalid repo — expected a path like owner/name" />;
  if (isNotFound(detail.error))
    return <PageError message={`${full} is not registered in embry0`} back={{ to: "/repos", label: "All repositories" }} />;
  if (detail.isError)
    return <PageError message={`Failed to load ${full}`} onRetry={() => void detail.refetch()} />;
  if (detail.isLoading || !detail.data) return <TableSkeleton />;
  // Readiness lands a few ms after the detail; painting between the two drew
  // the checklist's short placeholder, then pushed the page down (a layout
  // shift of 0.47 on a phone). Wait for its first answer or first failed
  // attempt, read from the stored update times: the checklist's own mount
  // refetches a failed query, and waiting on isLoading would unmount it again.
  const readinessAnswered =
    readiness.dataUpdatedAt > 0 || readiness.errorUpdatedAt > 0 || readiness.failureCount > 0;
  if (!readinessAnswered || !linkedPainted) return <TableSkeleton />;

  const repo = detail.data;

  return (
    <div className="space-y-6">
      <div>
        <Link to="/repos" className="text-sm text-white/50 hover:underline">
          ← Repositories
        </Link>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1">
          <h1 className="title-pixel text-xl font-bold">{full}</h1>
          <Badge tone="neutral" data-testid="repo-detail-forge">
            {providerLabel(repo.forge)}
          </Badge>
          {readiness.data && <ReadinessChip readiness={readiness.data} />}
        </div>
        {!repo.active && (
          <p className="mt-1 text-xs text-muted-foreground">Hidden from lists. embry0 still works on this repository.</p>
        )}
        {repo.notes && <p className="mt-1 text-sm text-white/60">{repo.notes}</p>}
      </div>

      <SetupChecklist repo={full} forge={repo.forge} />

      <section className="space-y-3">
        <div className="space-y-1">
          <h2 id="advanced" className="text-section font-semibold">
            Advanced settings
          </h2>
          <p className="text-sm text-fg-muted">
            Exact controls for people who know the values. Most repositories never need them.
          </p>
        </div>

        <AdvancedSection setting="testSetup">
          <QaConfigEditor repo={full} showIntro={false} />
        </AdvancedSection>

        <AdvancedSection setting="appSettings">
          <div className="flex">
            <Link
              to={`/environments?repo=${encodeURIComponent(full)}`}
              className="ml-auto text-sm text-fg hover:text-primary hover:underline"
              data-testid="repo-env-manage-link"
            >
              Add or change values
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
        </AdvancedSection>

        <AdvancedSection setting="workMachine">
          <RepoPreferencesSection repo={full} showIntro={false} />
        </AdvancedSection>

        <AdvancedSection setting="prReview">
          <PullRequestReviewCard repo={full} showIntro={false} />
        </AdvancedSection>

        <AdvancedSection setting="accounts">
          <RepoAccountsSection repo={repo.repo} forge={repo.forge} />
        </AdvancedSection>

        {/* Only a repository that holds the grant, or whose stored setup asks for it, has anything to see here. */}
        {(repo.docker_access === true || readiness.data?.needs_docker === true) && (
          <AdvancedSection setting="docker">
            <RepoDockerAccessSection repo={repo.repo} dockerAccess={repo.docker_access === true} />
          </AdvancedSection>
        )}

        {/* A server older than standards sends none, and has no design checks to show. */}
        {repo.standards && repo.standards.length > 0 && (
          <AdvancedSection setting="designStandard">
            <RepoStandardsRow repo={repo.repo} standards={repo.standards} />
          </AdvancedSection>
        )}
      </section>

      <HistoryPanel repo={full} />

      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <ListChecks className="h-4 w-4 text-fg-muted" />
          <h2 className="text-section font-semibold">Recent test runs</h2>
        </div>
        {(runs.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">No test runs yet.</p>
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
                    <td className="w-full whitespace-nowrap">
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
                      {formatDateTime(run.started_at)}
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
