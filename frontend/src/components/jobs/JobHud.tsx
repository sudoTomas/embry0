import { formatCost } from "@/lib/utils";
import { useElapsedTime } from "@/hooks/useElapsedTime";
import { Meter } from "@/components/ui/Meter";

interface JobHudProps {
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
  costUsd: number;
  turns: number;
  tokensIn: number;
  tokensOut: number;
  /** `pipeline_config.max_budget_usd`, else the graph's `max_total_budget_usd`; null or 0 means no meter. */
  budgetUsd: number | null | undefined;
}

/**
 * The HUD bar (spec 3.5): status, elapsed, cost, turns, tokens in and out in
 * the pixel face, and a budget meter when the job has a budget. Wraps to two
 * rows on a phone. The elapsed ticker lives here, so its once-a-second
 * re-render stays inside the bar instead of re-rendering the whole page.
 */
export function JobHud({ status, startedAt, finishedAt, costUsd, turns, tokensIn, tokensOut, budgetUsd }: JobHudProps) {
  const elapsed = useElapsedTime(startedAt, finishedAt);
  const hasBudget = typeof budgetUsd === "number" && budgetUsd > 0;
  return (
    <div data-testid="job-hud" className="hud flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border border-border bg-card px-3 py-2 text-sm">
      <Reading label="Status" id="status" value={status} />
      <Reading label="Elapsed" id="elapsed" value={elapsed || "—"} />
      <Reading label="Cost" id="cost" value={formatCost(costUsd)} />
      <Reading label="Turns" id="turns" value={String(turns)} />
      <Reading label="In" id="tokens-in" value={tokensIn.toLocaleString()} />
      <Reading label="Out" id="tokens-out" value={tokensOut.toLocaleString()} />
      {hasBudget && (
        <div className="flex items-center gap-2 basis-full sm:basis-auto sm:ml-auto min-w-[160px]">
          <span className="text-muted-foreground text-xs">Budget</span>
          <Meter value={costUsd} max={budgetUsd} label="Budget" className="flex-1" />
          <span className="text-xs">{formatCost(budgetUsd)}</span>
        </div>
      )}
    </div>
  );
}

function Reading({ label, id, value }: { label: string; id: string; value: string }) {
  return (
    <span className="flex items-baseline gap-1.5">
      <span className="text-muted-foreground text-xs">{label}</span>
      <span data-hud={id}>{value}</span>
    </span>
  );
}
