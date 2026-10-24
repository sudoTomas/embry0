import { useMemo } from "react";
import { Link } from "react-router";
import { FileSearch, RefreshCw } from "lucide-react";
import type { ContextAuditReason, ContextAuditRepo, ContextAuditSurvey } from "@/api/orgs";
import { toastApiError } from "@/api/errorToast";
import { formatErrorDetail } from "@/api/errorDetail";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { PageError } from "@/components/PageError";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useContextAudit, useRefreshContextAudit } from "@/hooks/useOrgs";
import type { JobStatus } from "@/lib/types";
import { cn, formatCost, formatDate, formatDateTime } from "@/lib/utils";
import { TABLE_BOX, TD, TH } from "./styles";

const NEXT: Record<ContextAuditReason, { label: string; tone: BadgeTone; title: string }> = {
  first: { label: "Due: never audited", tone: "warning", title: "Starts on the schedule's next round" },
  changed: { label: "Due: files changed", tone: "warning", title: "An instruction file changed since the last audit" },
  aged: { label: "Due: interval passed", tone: "warning", title: "The interval passed and the branch moved since" },
  live: { label: "Running", tone: "info", title: "An audit of this repository is pending or running" },
  current: { label: "Up to date", tone: "neutral", title: "Audited since its instruction files last changed" },
  off: { label: "Schedule off", tone: "neutral", title: "The schedule is off: audits start only by hand" },
};

/** Why an audit failed, in a few words, for the codes an audit meets; any other code is shown as it is. */
const FAILURE: Record<string, string> = {
  ERR_BUDGET_REFUSED: "refused by the budget gate",
  ERR_BUDGET_OVERRUN: "stopped at its budget",
  ERR_AGENT_MAX_TURNS: "stopped at the turn limit",
};

const MISSING = "-";

/** Most outdated claims first; a repository whose count is unknown after them, then by name. */
function byStale(a: ContextAuditRepo, b: ContextAuditRepo): number {
  const sa = a.newest_job?.stale ?? -1;
  const sb = b.newest_job?.stale ?? -1;
  return sb - sa || a.repo.localeCompare(b.repo);
}

function Totals({ survey }: { survey: ContextAuditSurvey }) {
  const jobs = survey.repos.map((r) => r.newest_job).filter((j) => j !== null);
  const audited = jobs.filter((j) => j.status === "completed");
  const stale = audited.reduce((sum, j) => sum + (j.stale ?? 0), 0);
  const failed = jobs.filter((j) => j.status === "failed").length;
  const cost = jobs.reduce((sum, j) => sum + (j.total_cost_usd ?? 0), 0);
  return (
    <p className="text-sm text-fg-secondary" data-testid="context-audit-totals">
      {survey.repos.length} {survey.repos.length === 1 ? "repository" : "repositories"} with instruction files
      {` · ${audited.length} audited`}
      {` · ${stale} outdated ${stale === 1 ? "claim" : "claims"}`}
      {failed > 0 && ` · ${failed} failed`}
      {` · ${formatCost(cost)} on the latest audits`}
    </p>
  );
}

function LatestAudit({ row }: { row: ContextAuditRepo }) {
  const job = row.newest_job;
  if (!job) {
    return row.refused_at ? (
      <span className="text-xs text-warning">Start refused {formatDateTime(row.refused_at)}</span>
    ) : (
      <span className="text-xs text-muted-foreground">Never</span>
    );
  }
  const when = job.finished_at ?? job.created_at;
  return (
    <div className="flex flex-col gap-0.5">
      <Link to={`/jobs/${job.job_id}`} className="flex items-center gap-2 hover:underline" data-testid="context-audit-job">
        <JobStatusBadge status={job.status as JobStatus} />
        {when && <span className="whitespace-nowrap text-xs text-white/60">{formatDateTime(when)}</span>}
      </Link>
      {job.status === "failed" && job.error_code && (
        <span className="text-xs text-destructive" data-testid="context-audit-failure">
          {FAILURE[job.error_code] ?? job.error_code}
        </span>
      )}
      {job.pr_url && (
        <a
          href={job.pr_url}
          target="_blank"
          rel="noreferrer"
          className="text-xs text-info hover:underline"
          data-testid="context-audit-pr"
        >
          Pull request with the fixes
        </a>
      )}
    </div>
  );
}

function Stale({ row }: { row: ContextAuditRepo }) {
  const job = row.newest_job;
  if (job?.status !== "completed") return <span className="text-muted-foreground">{MISSING}</span>;
  if (job.stale === null) {
    return (
      <span className="text-warning" title="The report lost its count: open the job to read it">
        ?
      </span>
    );
  }
  return <span className={cn(job.stale > 0 ? "text-fg" : "text-white/50")}>{job.stale}</span>;
}

/**
 * Each registered repository with files that steer a coding agent: its
 * newest audit, the outdated claims that audit found, and whether the
 * schedule will audit it again. The findings themselves are on the job's
 * page.
 */
export function ContextAuditTable({ survey }: { survey: ContextAuditSurvey }) {
  const rows = useMemo(() => [...survey.repos].sort(byStale), [survey.repos]);

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={FileSearch}
        title="No instruction files found"
        description="None of this organization's registered repositories has a CLAUDE.md, AGENTS.md, skill or rules file on its default branch."
      />
    );
  }

  return (
    <div className="space-y-2">
      <Totals survey={survey} />
      <div className={TABLE_BOX}>
        <table className="data-table sm:min-w-[820px]" data-testid="context-audit-table">
          <thead>
            <tr>
              <th scope="col" className={TH}>Repository</th>
              <th scope="col" className={cn(TH, "text-right")}>Outdated claims</th>
              <th scope="col" className={TH}>Latest audit</th>
              <th scope="col" className={cn(TH, "hidden text-right sm:table-cell")}>Files</th>
              <th scope="col" className={cn(TH, "hidden text-right sm:table-cell")}>Cost</th>
              <th scope="col" className={TH}>Next</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const job = row.newest_job;
              const next = NEXT[row.reason];
              return (
                <tr key={row.repo} data-testid="context-audit-row">
                  <td className={cn(TD, "[overflow-wrap:anywhere]")}>
                    <Link to={`/repos/${row.repo}`} className="font-medium hover:underline" data-testid="context-audit-repo">
                      {row.repo}
                    </Link>
                  </td>
                  <td className={cn(TD, "text-right font-mono")} data-testid="context-audit-stale">
                    <Stale row={row} />
                  </td>
                  <td className={TD}>
                    <LatestAudit row={row} />
                  </td>
                  <td className={cn(TD, "hidden text-right font-mono text-white/70 sm:table-cell")}>
                    {job?.files_read ?? row.files}
                  </td>
                  <td className={cn(TD, "hidden text-right font-mono text-white/70 sm:table-cell")}>
                    {job?.total_cost_usd != null ? formatCost(job.total_cost_usd) : MISSING}
                  </td>
                  <td className={TD} data-testid="context-audit-next">
                    <Badge tone={next.tone} title={next.title}>
                      {next.label}
                    </Badge>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {survey.unread.length > 0 && (
        <ul className="space-y-1 text-xs text-warning" data-testid="context-audit-unread">
          {survey.unread.map((u) => (
            <li key={u.repo}>
              {u.repo} was not read: {u.detail}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function loadError(err: unknown): string {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status === 503) return "The context audit is not running on this server.";
  return formatErrorDetail(response?.data?.detail) ?? "The context audit did not load.";
}

/** The organization's context audit tab: the schedule, the table, and a fresh read of the forge on request. */
export function ContextAuditTab({ owner, days }: { owner: string; days: number | null }) {
  const survey = useContextAudit(owner);
  const refresh = useRefreshContextAudit(owner);

  const read = () =>
    refresh.mutate(undefined, { onError: (e) => toastApiError(e, "The repositories were not read again") });

  return (
    <section className="min-w-0 space-y-3" data-testid="context-audit-tab">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm text-fg-secondary" data-testid="context-audit-schedule">
          {days === null
            ? "The schedule is off: audits start only by hand. Turn it on under Settings."
            : `Each repository is audited when its instruction files change, and again after ${days} ${days === 1 ? "day" : "days"} once its branch has moved.`}
        </p>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="ml-auto"
          onClick={read}
          disabled={refresh.isPending || survey.isLoading}
          data-testid="context-audit-refresh"
        >
          <RefreshCw className={cn("h-4 w-4", refresh.isPending && "animate-spin")} />
          {refresh.isPending ? "Reading…" : "Read again"}
        </Button>
      </div>
      {survey.data?.read_at && (
        <p className="text-xs text-muted-foreground" data-testid="context-audit-read-at">
          Default branches read {formatDate(survey.data.read_at)}
        </p>
      )}
      {survey.isError ? (
        <PageError message={loadError(survey.error)} onRetry={() => void survey.refetch()} />
      ) : survey.data ? (
        <ContextAuditTable survey={survey.data} />
      ) : (
        <Skeleton className="h-32 w-full" />
      )}
    </section>
  );
}
