import type { ReactNode } from "react";
import { Link } from "react-router";
import { useStats } from "@/hooks/useStats";
import { useQueue } from "@/hooks/useQueue";
import { useWatcherRuns } from "@/hooks/useWatcherRuns";
import { Badge } from "@/components/ui/Badge";
import { formatDateTime } from "@/lib/utils";
import { statusLabel, statusTone } from "@/lib/status";
import { watcherActionLabel, watcherActionTone } from "@/lib/watcherActions";
import type { RecentIssue } from "@/lib/types";
import {
  SingleSourceTile,
  type SingleSourceQuery,
} from "@/components/vitals";

// Format helpers — kept local; they're the only translation layer between
// raw backend numbers and tile strings. A tile counting up draws every step
// through them, so a count rounds to whole numbers on the way.
const formatCount = (n: number) => String(Math.round(n));
const formatPercent = (n: number) => `${Math.round(n * 100)}%`;
const formatUsd = (n: number) => `$${n.toFixed(2)}`;

// Project a useQuery result onto the SingleSourceQuery contract with a
// per-tile value selector. Keeping the selector at the call site means
// each tile names its own source field, which makes the assay's
// source-mapping guards trivially auditable.
function selectQuery<TSource, TValue>(
  result: { data: TSource | undefined; isPending: boolean; isError: boolean },
  select: (source: TSource) => TValue,
): SingleSourceQuery<TValue> {
  return {
    isError: result.isError,
    isPending: result.isPending,
    data: result.data === undefined ? undefined : select(result.data),
  };
}

/** How a recent issue is named: owner/repo#42 on GitHub, the tracker's own
 * key (ENG-7) elsewhere, the repository when the issue has no key. */
function recentIssueKey(issue: RecentIssue): string {
  if (issue.tracker_key) return issue.tracker === "github" ? `${issue.repo}#${issue.tracker_key}` : issue.tracker_key;
  return issue.issue_number > 0 ? `${issue.repo}#${issue.issue_number}` : issue.repo;
}

/** The issue's title without a leading "[KEY]" that repeats the key beside it. */
function recentIssueTitle(issue: RecentIssue): string {
  const title = issue.title ?? "";
  const prefix = issue.tracker_key ? `[${issue.tracker_key}]` : "";
  return prefix && title.startsWith(prefix) ? title.slice(prefix.length).trimStart() : title;
}

/**
 * Overview — every number on this page comes from embry0's own backend: live
 * counts from /queue, the rest from /stats, plus the watcher audit trail. The
 * brief line above it (what waits for a person) is the layout's, on this
 * route only.
 */
export function DashboardPage() {
  const stats = useStats();
  const queue = useQueue();
  const watcher = useWatcherRuns(5);

  const recentIssues = stats.data?.recent_issues.slice(0, 6) ?? [];

  return (
    <div className="space-y-5">
      <div>
        {/* One row of six on desktop. #280: how the job itself ended and
            whether its work was delivered stay two numbers, never one — the
            blended "succeeded or merged" rate repeated both and is gone. */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
          <SingleSourceTile
            label="Running"
            turn={0}
            query={selectQuery(queue, (q) => q.running)}
            format={formatCount}
            tone={(n) => (n > 0 ? "running" : "neutral")}
            detail={queue.data ? `${queue.data.pending} queued` : undefined}
          />
          {/* The page's one gold: the count of jobs waiting for a person,
              the single thing on Overview to act on. */}
          <SingleSourceTile
            label="Needs you"
            turn={1}
            query={selectQuery(queue, (q) => q.awaiting_input + q.paused)}
            format={formatCount}
            tone={(n) => (n > 0 ? "accent" : "neutral")}
          />
          <SingleSourceTile
            label="Job success"
            turn={2}
            query={selectQuery(stats, (s) => s.job_success_rate ?? s.success_rate)}
            format={formatPercent}
          />
          {/* Distinct pull requests, not job rows — several jobs share one PR. */}
          <SingleSourceTile
            label="PRs merged"
            turn={3}
            query={selectQuery(stats, (s) => s.delivery?.prs_merged ?? 0)}
            format={formatCount}
            tone={(n) => (n > 0 ? "good" : "neutral")}
          />
          <SingleSourceTile
            label="Cost today"
            turn={4}
            query={selectQuery(stats, (s) => s.daily_cost_usd)}
            format={formatUsd}
            detail={stats.data ? `${formatUsd(stats.data.monthly_cost_usd)} this month` : undefined}
          />
          <SingleSourceTile
            label="Cache hit rate"
            turn={5}
            query={selectQuery(stats, (s) => s.token_totals?.cache_hit_rate ?? 0)}
            format={formatPercent}
          />
        </div>
        <p className="mt-2 text-xs text-fg-muted">
          PRs merged counts distinct pull requests from embry0 branches, human-finished ones included.
        </p>
      </div>

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
        <DashboardPanel
          title="Recent issues"
          viewAll={{ to: "/issues", label: "All issues" }}
          isPending={stats.isPending}
          isError={stats.isError}
          isEmpty={recentIssues.length === 0}
          emptyText="No issues processed yet."
        >
          {/* On a phone the When column, the last, steps aside so the issue
              keeps its key and title readable on up to two lines. */}
          <table className="data-table [&_tr>*:last-child]:hidden sm:[&_tr>*:last-child]:table-cell">
            <thead>
              <tr>
                <th scope="col">Status</th>
                <th scope="col">Issue</th>
                <th scope="col" className="num hidden sm:table-cell">Cost</th>
                <th scope="col" className="num">When</th>
              </tr>
            </thead>
            <tbody>
              {recentIssues.map((issue) => {
                const status = issue.issue_status ?? issue.status ?? (issue.passed ? "passed" : "failed");
                return (
                  <tr key={issue.trace_id}>
                    <td className="w-px">
                      <Badge tone={statusTone(status)}>{statusLabel(status)}</Badge>
                    </td>
                    <td className="w-full max-w-0">
                      {/* On a phone the key takes a line of its own and the
                          title up to two under it; from sm up they share one. */}
                      <Link
                        to={`/issues/${issue.trace_id}`}
                        className="block break-words hover:underline sm:truncate"
                        title={issue.title}
                      >
                        <span className="block font-mono text-xs text-fg-secondary sm:inline">{recentIssueKey(issue)}</span>
                        {recentIssueTitle(issue) && (
                          <span className="line-clamp-2 sm:ml-2 sm:inline">{recentIssueTitle(issue)}</span>
                        )}
                      </Link>
                    </td>
                    <td className="num hidden whitespace-nowrap text-fg-secondary sm:table-cell">
                      {issue.cost_usd !== undefined ? formatUsd(issue.cost_usd) : "—"}
                    </td>
                    <td className="num whitespace-nowrap text-fg-muted">{formatDateTime(issue.timestamp)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </DashboardPanel>

        <DashboardPanel
          title="Watcher"
          viewAll={{ to: "/watcher", label: "All ticks" }}
          isPending={watcher.isPending}
          isError={watcher.isError}
          isEmpty={(watcher.data?.length ?? 0) === 0}
          emptyText="No watcher ticks recorded yet."
        >
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Result</th>
                <th scope="col">Detail</th>
                <th scope="col" className="num">When</th>
              </tr>
            </thead>
            <tbody>
              {watcher.data?.map((run) => (
                <tr key={run.id}>
                  <td className="w-px">
                    <Badge tone={watcherActionTone(run.action)}>
                      {watcherActionLabel(run.action)}
                    </Badge>
                  </td>
                  <td className="w-full max-w-0 truncate text-fg-secondary">
                    {run.tracker_key ?? `${run.log_lines} log lines`}
                  </td>
                  <td className="num whitespace-nowrap text-fg-muted">{formatDateTime(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </DashboardPanel>
      </div>
    </div>
  );
}

interface DashboardPanelProps {
  title: string;
  viewAll?: { to: string; label: string };
  isPending: boolean;
  isError: boolean;
  isEmpty: boolean;
  emptyText: string;
  children: ReactNode;
}

function DashboardPanel({
  title,
  viewAll,
  isPending,
  isError,
  isEmpty,
  emptyText,
  children,
}: DashboardPanelProps) {
  let body: ReactNode;
  if (isError) {
    body = <p className="px-3 py-3 text-sm text-muted-foreground">Unavailable</p>;
  } else if (isPending) {
    body = <p className="px-3 py-3 text-sm text-muted-foreground">Loading…</p>;
  } else if (isEmpty) {
    body = <p className="px-3 py-3 text-sm text-muted-foreground">{emptyText}</p>;
  } else {
    body = children;
  }

  return (
    <section
      data-testid={`panel-${title}`}
      className="overflow-hidden rounded-lg border border-border bg-card"
    >
      <header className="flex items-center justify-between border-b border-border px-3 py-2.5">
        <h2 className="text-section font-semibold">{title}</h2>
        {viewAll && (
          <Link
            to={viewAll.to}
            className="text-sm text-fg transition-colors hover:text-primary"
          >
            {viewAll.label} →
          </Link>
        )}
      </header>
      <div className="overflow-x-auto">{body}</div>
    </section>
  );
}
