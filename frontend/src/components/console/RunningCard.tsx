import { memo } from "react";
import { useLiveDuration } from "@/hooks/useLiveDuration";
import { formatDuration, getColors } from "@/lib/agentVisuals";
import { useLiveJobSummary } from "@/hooks/useLiveJobSummary";
import { formatCost } from "@/lib/utils";
import type { JobResponse } from "@/lib/types";
import { ConsoleCardShell } from "./ConsoleCardShell";

/** Budget-meter thresholds per the board spec: amber from 80% of cap, red at
 * overrun. Intentionally louder-later than getCostBarColor (which warns at
 * 50%) — a board card should only get loud when action is actually near. */
function budgetBarColor(ratio: number): string {
  if (ratio >= 1) return "bg-destructive";
  if (ratio >= 0.8) return "bg-warning";
  return "bg-success";
}

interface RunningCardProps {
  job: JobResponse;
  /** Budget cap for the spent/cap meter. Null/undefined ⇒ no cap, no meter. */
  maxBudgetUsd?: number | null;
}

/**
 * Live board card for a running job, fed by useLiveJobSummary (one WS per
 * card — fine at single-digit sandbox concurrency). Degrades gracefully to
 * polled data when the WS is down: cost falls back to the jobs row, the
 * stage badge falls back to job.current_stage (without the `#attempt`
 * suffix — attempt counts only exist in the event stream), and the card
 * never renders blank.
 *
 * Memoized so the 5s jobs poll re-renders only cards whose row actually
 * changed (react-query's structural sharing keeps identity stable).
 */
export const RunningCard = memo(function RunningCard({ job, maxBudgetUsd }: RunningCardProps) {
  const summary = useLiveJobSummary(job.job_id);
  const elapsedMs = useLiveDuration(job.started_at ?? undefined, true, 0);

  // Live values first, polled row as fallback — never blank.
  const stageLabel = summary.currentNode
    ? `${summary.currentNode}#${summary.attempt}`
    : job.current_stage ?? null;
  const stageColors = getColors(summary.currentNode ?? job.current_stage ?? "");
  const spentUsd = summary.latestCost > 0 ? summary.latestCost : job.total_cost_usd;
  const cap = maxBudgetUsd != null && maxBudgetUsd > 0 ? maxBudgetUsd : null;
  const budgetRatio = cap != null ? spentUsd / cap : null;

  return (
    <ConsoleCardShell
      job={job}
      headerRight={
        <span data-testid="live-indicator" className="inline-flex items-center gap-1.5 text-[10px]">
          <span
            className={`h-1.5 w-1.5 rounded-full ${summary.isConnected ? "bg-green-400 animate-pulse" : "bg-white/20"}`}
          />
          <span className={summary.isConnected ? "text-green-400" : "text-white/30"}>
            {summary.isConnected ? "Live" : "Polling"}
          </span>
        </span>
      }
    >
      <div className="mt-2 flex items-center gap-2 min-w-0">
        {stageLabel && (
          <span
            data-testid="stage-badge"
            className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] font-mono ${stageColors.text} ${stageColors.bg} ${stageColors.border}`}
          >
            {stageLabel}
          </span>
        )}
        <span data-testid="activity-ticker" className="font-mono text-[11px] text-white/50 truncate">
          {summary.lastActivity ?? "waiting for activity…"}
        </span>
      </div>
      <div className="mt-2 flex items-center gap-3 text-[11px] text-white/40">
        <span data-testid="cost-line">
          {cap != null ? `${formatCost(spentUsd)} / ${formatCost(cap)}` : formatCost(spentUsd)}
        </span>
        {job.started_at && <span>{formatDuration(elapsedMs)}</span>}
      </div>
      {budgetRatio != null && (
        <div data-testid="budget-meter" className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/[0.06]">
          <div
            data-testid="budget-meter-bar"
            className={`h-full rounded-full ${budgetBarColor(budgetRatio)}`}
            style={{ width: `${Math.min(budgetRatio, 1) * 100}%` }}
          />
        </div>
      )}
    </ConsoleCardShell>
  );
});
