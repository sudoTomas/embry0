import { useStats } from "@/hooks/useStats";
import { useQueue } from "@/hooks/useQueue";
import { StatCard } from "@/components/stats/StatCard";
import { TierBreakdown } from "@/components/stats/TierBreakdown";
import { CostBreakdown } from "@/components/stats/CostBreakdown";
import { FailureCategories } from "@/components/stats/FailureCategories";
import { SuccessSparkline } from "@/components/stats/SuccessSparkline";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { PageError } from "@/components/PageError";
import { DashboardSkeleton } from "@/components/ui/PageSkeleton";
import { TIER_COLORS } from "@/lib/constants";
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

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold animate-fade-up">Dashboard</h1>

      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard title="Total Issues" value={String(stats.total_issues)} color="#3b82f6" delay={0} />
        <StatCard title="Success Rate" value={formatPercent(stats.success_rate)} color="#22c55e" delay={60} />
        <StatCard
          title="Total Cost"
          value={formatCost(stats.total_cost_usd)}
          subtitle={`Today: ${formatCost(stats.daily_cost_usd)} · Month: ${formatCost(stats.monthly_cost_usd)}`}
          color="#f59e0b"
          delay={120}
        />
        <StatCard title="Queue Depth" value={String(queue?.depth ?? stats.queue_depth)} color="#a855f7" delay={180} />
      </div>

      {/* Success sparkline */}
      <Card className="animate-fade-up" style={{ animationDelay: '240ms' }}>
        <CardContent className="p-4 flex items-center gap-4">
          <span className="text-sm font-medium text-muted-foreground whitespace-nowrap">
            Recent Success Rate
          </span>
          <SuccessSparkline recentIssues={stats.recent_issues} />
        </CardContent>
      </Card>

      {/* Cost breakdown + Failure categories side by side */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 animate-fade-up" style={{ animationDelay: '300ms' }}>
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

      {/* Recent issues */}
      <Card className="animate-fade-up" style={{ animationDelay: '360ms' }}>
        <CardHeader>
          <CardTitle className="text-lg">Recent Issues</CardTitle>
        </CardHeader>
        <CardContent>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th scope="col" className="text-left py-2">#</th>
                <th scope="col" className="text-left py-2">Repo</th>
                <th scope="col" className="text-left py-2">Tier</th>
                <th scope="col" className="text-left py-2">Status</th>
                <th scope="col" className="text-right py-2">Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {stats.recent_issues.map((issue) => (
                <tr key={issue.trace_id} className="border-b border-border/50 hover:bg-white/[0.02] transition-colors">
                  <td className="py-2">{issue.issue_number}</td>
                  <td className="py-2 font-mono text-xs">{issue.repo}</td>
                  <td className={`py-2 capitalize ${TIER_COLORS[issue.tier]}`}>{issue.tier}</td>
                  <td className="py-2">
                    <span className={issue.passed ? "text-success" : "text-destructive"}>
                      {issue.passed ? "Passed" : "Failed"}
                    </span>
                  </td>
                  <td className="text-right py-2 text-muted-foreground">{formatDate(issue.timestamp)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
