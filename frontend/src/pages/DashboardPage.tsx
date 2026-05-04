import { useStats } from "@/hooks/useStats";
import { useQueue } from "@/hooks/useQueue";
import { CompactStatCard } from "@/components/stats/CompactStatCard";
import { TierBreakdown } from "@/components/stats/TierBreakdown";
import { CostBreakdown } from "@/components/stats/CostBreakdown";
import { FailureCategories } from "@/components/stats/FailureCategories";
import { SuccessSparkline } from "@/components/stats/SuccessSparkline";
import { LiveActivityBand } from "@/components/dashboard/LiveActivityBand";
import { CostByRepoBars } from "@/components/dashboard/CostByRepoBars";
import { TopExpensiveIssues } from "@/components/dashboard/TopExpensiveIssues";
import { Badge } from "@/components/ui/Badge";
import { PageError } from "@/components/PageError";
import { DashboardSkeleton } from "@/components/ui/PageSkeleton";
import { GettingStartedCard } from "@/components/dashboard/GettingStartedCard";
import { formatCost, formatPercent, formatDate } from "@/lib/utils";

export function DashboardPage() {
  const { data: stats, isLoading, isError, refetch } = useStats();
  const { data: queue } = useQueue();

  if (isError) {
    return <PageError message="Failed to load dashboard stats" onRetry={() => refetch()} />;
  }

  if (isLoading || !stats) {
    return <DashboardSkeleton />;
  }

  // Live state — prefer the dedicated /queue endpoint (5s refresh) over the
  // 30s stats refresh; fall back to stats fields when queue isn't loaded yet.
  const running = queue?.running ?? stats.running ?? 0;
  const queued = queue?.pending ?? stats.queued ?? 0;
  const awaitingInput = queue?.awaiting_input ?? stats.awaiting_input ?? 0;
  const paused = queue?.paused ?? stats.paused ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between animate-fade-up">
        <h1 className="text-2xl font-bold">Dashboard</h1>
        {paused > 0 && (
          <Badge tone="warning" title={`${paused} paused job(s)`}>
            {paused} paused
          </Badge>
        )}
      </div>

      {/* Dense 6-up stat strip — companion's tasks-view summary */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5">
        <CompactStatCard
          title="Running"
          value={String(running)}
          color="#22c55e"
          pulse={running > 0}
          delay={0}
        />
        <CompactStatCard
          title="Queued"
          value={String(queued)}
          color="#06b6d4"
          delay={40}
        />
        <CompactStatCard
          title="Awaiting Input"
          value={String(awaitingInput)}
          color="#f59e0b"
          pulse={awaitingInput > 0}
          delay={80}
        />
        <CompactStatCard
          title="Failed"
          value={String(stats.failed)}
          color="#ef4444"
          delay={120}
        />
        <CompactStatCard
          title="Spent Today"
          value={formatCost(stats.daily_cost_usd)}
          subtitle={`Month ${formatCost(stats.monthly_cost_usd)}`}
          color="#f97316"
          delay={160}
        />
        <CompactStatCard
          title="Total Issues"
          value={String(stats.total_issues)}
          subtitle={`${formatPercent(stats.success_rate)} pass rate`}
          color="#a855f7"
          delay={200}
        />
      </div>

      {/* Live activity band — only renders when something is happening */}
      <LiveActivityBand
        running={running}
        queued={queued}
        awaitingInput={awaitingInput}
      />

      {stats.total_issues === 0 ? (
        <GettingStartedCard />
      ) : (
        <>
          {/* Success sparkline */}
          {stats.recent_issues.length > 0 && (
            <div
              className="athanor-card p-4 animate-fade-up flex items-center gap-4"
              style={{ animationDelay: "240ms" }}
            >
              <span className="text-sm font-medium text-white/40 whitespace-nowrap">
                Recent Success Rate
              </span>
              <SuccessSparkline recentIssues={stats.recent_issues} />
            </div>
          )}

          {/* Cost by repo + Top expensive issues — companion-style breakdown */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <CostByRepoBars
              costByRepo={stats.cost_by_repo ?? []}
              totalCost={stats.total_cost_usd}
            />
            <TopExpensiveIssues issues={stats.top_expensive_issues ?? []} />
          </div>

          {/* Cost breakdown by tier + Failure categories */}
          <div
            className="grid grid-cols-1 lg:grid-cols-2 gap-4 animate-fade-up"
            style={{ animationDelay: "420ms" }}
          >
            <CostBreakdown
              costByTier={stats.cost_by_tier}
              dailyCost={stats.daily_cost_usd}
              monthlyCost={stats.monthly_cost_usd}
            />
            <FailureCategories categories={stats.failure_categories} />
          </div>

          {/* Tier breakdown */}
          <TierBreakdown
            costByTier={stats.cost_by_tier}
            successRateByTier={stats.success_rate_by_tier}
            avgAttemptsByTier={stats.avg_attempts_by_tier}
            avgCostPerTier={stats.avg_cost_per_tier}
          />

          {/* Recent issues — refactored to use Badge primitive */}
          <div
            className="athanor-card animate-fade-up"
            style={{
              animationDelay: "540ms",
              borderColor: "rgba(6,182,212,0.18)",
            }}
          >
            <div className="px-5 pt-4 pb-2 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-white">Recent Issues</h2>
              <span className="text-[11px] text-white/40">
                {stats.recent_issues.length} shown
              </span>
            </div>
            <div className="px-5 pb-4 overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-white/[0.06] text-white/40 text-[10px] uppercase tracking-wider">
                    <th scope="col" className="text-left py-2 font-medium">#</th>
                    <th scope="col" className="text-left py-2 font-medium">Repo</th>
                    <th scope="col" className="text-left py-2 font-medium">Status</th>
                    <th scope="col" className="text-right py-2 font-medium">Cost</th>
                    <th scope="col" className="text-right py-2 font-medium">Timestamp</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.recent_issues.map((issue) => (
                    <tr
                      key={issue.trace_id}
                      className="border-b border-white/[0.04] hover:bg-cyan-500/[0.02] transition-colors"
                    >
                      <td className="py-2 font-mono tabular-nums text-white/60">
                        {issue.issue_number || "—"}
                      </td>
                      <td className="py-2 font-mono text-white/80 truncate max-w-[200px]" title={issue.repo}>
                        {issue.repo}
                      </td>
                      <td className="py-2">
                        <Badge
                          tone={statusTone(issue.status, issue.passed)}
                          sigil={statusSigil(issue.status, issue.passed)}
                        >
                          {statusLabel(issue.status, issue.passed)}
                        </Badge>
                      </td>
                      <td className="py-2 text-right font-mono tabular-nums text-white/70">
                        {issue.cost_usd ? formatCost(issue.cost_usd) : "—"}
                      </td>
                      <td className="text-right py-2 text-muted-foreground tabular-nums">
                        {formatDate(issue.timestamp)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function statusTone(
  status: string | undefined,
  passed: boolean,
): "success" | "warning" | "error" | "info" | "neutral" {
  if (status === "running") return "info";
  if (status === "awaiting_input") return "warning";
  if (status === "paused") return "warning";
  if (status === "failed") return "error";
  if (status === "completed" || passed) return "success";
  return "neutral";
}

function statusLabel(status: string | undefined, passed: boolean): string {
  if (status === "running") return "Running";
  if (status === "awaiting_input") return "Awaiting Input";
  if (status === "paused") return "Paused";
  if (status === "failed") return "Failed";
  if (status === "completed" || passed) return "Passed";
  return status ?? "—";
}

function statusSigil(
  status: string | undefined,
  passed: boolean,
): import("@/lib/sigils").Stage | undefined {
  // Operator-critical states (failed) skip divine flourishes per divine/CLAUDE.md.
  if (status === "running") return "develop"; // Sulphur — active fire
  if (status === "awaiting_input") return "triage"; // Mercury — the messenger
  if (status === "paused") return "validate"; // Salt — fixed/stable
  if (status === "completed" || passed) return "publish"; // Sol — the gold
  return undefined;
}
