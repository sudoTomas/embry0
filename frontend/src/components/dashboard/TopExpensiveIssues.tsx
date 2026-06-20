import { Link } from "react-router";
import type { ExpensiveIssue } from "@/lib/types/stats";
import { formatCost } from "@/lib/utils";

interface TopExpensiveIssuesProps {
  issues: ExpensiveIssue[];
}

/**
 * Top 5 most expensive issues — companion's "Top 5 Most Expensive Tasks" panel,
 * mapped onto embry0's issue dimension. Sums total_cost_usd across all
 * jobs per issue.
 */
export function TopExpensiveIssues({ issues }: TopExpensiveIssuesProps) {
  if (!issues || issues.length === 0) {
    return (
      <div className="athanor-card px-5 py-4">
        <h2 className="text-sm font-semibold text-white mb-3">Top Expensive Issues</h2>
        <p className="text-xs text-white/30">No spend yet.</p>
      </div>
    );
  }

  return (
    <div className="athanor-card px-5 py-4 animate-fade-up" style={{ animationDelay: "360ms" }}>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-white">Top Expensive Issues</h2>
        <span className="text-[11px] text-white/40">Sum across all jobs</span>
      </div>
      <ol className="space-y-1.5">
        {issues.map((issue, idx) => (
          <li key={issue.trace_id}>
            <Link
              to={`/issues/${issue.trace_id}`}
              className="grid grid-cols-[auto_1fr_auto_auto] items-center gap-3 px-2 py-1.5 -mx-2 rounded-md text-xs hover:bg-white/[0.03] transition-colors"
            >
              <span className="font-mono text-white/30 tabular-nums w-6">#{idx + 1}</span>
              <span className="text-white/85 truncate" title={issue.title}>
                {issue.title}
              </span>
              <span className="font-mono text-[10px] text-white/40 truncate" title={issue.repo}>
                {issue.repo}
              </span>
              <span className="font-mono tabular-nums text-primary text-right min-w-[60px]">
                {formatCost(issue.cost_usd)}
              </span>
            </Link>
          </li>
        ))}
      </ol>
    </div>
  );
}
