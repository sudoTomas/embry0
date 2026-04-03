import type { RecentIssue } from "@/lib/types";

interface SuccessSparklineProps {
  /** Recent issues in chronological order (oldest first). */
  recentIssues: RecentIssue[];
  /** Maximum number of dots to display (default 20). */
  maxDots?: number;
}

/**
 * Compact sparkline showing pass/fail history as colored dots.
 * Green = passed, red = failed. Most recent is on the right.
 */
export function SuccessSparkline({ recentIssues, maxDots = 20 }: SuccessSparklineProps) {
  // Take the most recent N issues, keeping chronological order (oldest → newest)
  const issues = recentIssues.slice(-maxDots);

  if (issues.length === 0) {
    return <span className="text-xs text-muted-foreground">No data</span>;
  }

  // Compute rolling success rate for label
  const passCount = issues.filter((i) => i.passed).length;
  const rate = ((passCount / issues.length) * 100).toFixed(0);

  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-0.5" title={`Last ${issues.length}: ${rate}% success`}>
        {issues.map((issue, idx) => (
          <span
            key={issue.trace_id || idx}
            className={`inline-block h-2.5 w-2.5 rounded-full ${
              issue.passed ? "bg-success" : "bg-destructive"
            }`}
            title={`#${issue.issue_number} — ${issue.passed ? "passed" : "failed"}`}
          />
        ))}
      </div>
      <span className="text-xs text-muted-foreground whitespace-nowrap">
        {rate}% ({passCount}/{issues.length})
      </span>
    </div>
  );
}
