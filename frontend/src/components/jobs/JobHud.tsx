import { formatCost } from "@/lib/utils";

interface JobHudProps {
  status: string;
  elapsed: string;
  costUsd: number;
  turns: number;
  tokensIn: number;
  tokensOut: number;
  /** `pipeline_config.max_budget_usd`, else the graph's `max_total_budget_usd`; null or 0 means no meter. */
  budgetUsd: number | null | undefined;
}

type Level = "ok" | "warn" | "over";

function levelOf(cost: number, budget: number): Level {
  const ratio = cost / budget;
  if (ratio >= 1) return "over";
  if (ratio >= 0.8) return "warn";
  return "ok";
}

const LEVEL_CLASS: Record<Level, string> = {
  ok: "bg-palette-emerald",
  warn: "bg-palette-amber",
  over: "bg-destructive",
};

/**
 * The HUD bar (spec 3.5): status, elapsed, cost, turns, tokens in and out in
 * the pixel face, and a budget meter when the job has a budget. Wraps to two
 * rows on a phone.
 */
export function JobHud({ status, elapsed, costUsd, turns, tokensIn, tokensOut, budgetUsd }: JobHudProps) {
  const hasBudget = typeof budgetUsd === "number" && budgetUsd > 0;
  const level = hasBudget ? levelOf(costUsd, budgetUsd) : "ok";
  const pct = hasBudget ? Math.min(100, Math.round((costUsd / budgetUsd) * 100)) : 0;
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
          <div
            role="meter"
            aria-label="Budget"
            aria-valuemin={0}
            aria-valuemax={budgetUsd}
            aria-valuenow={costUsd}
            data-level={level}
            className="h-2 flex-1 rounded-full bg-muted overflow-hidden"
          >
            <div className={`h-full ${LEVEL_CLASS[level]}`} style={{ width: `${pct}%` }} />
          </div>
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
