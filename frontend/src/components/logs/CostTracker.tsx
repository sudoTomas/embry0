import { Card, CardContent } from "@/components/ui/Card";
import { formatCost, getCostColor, getCostBarColor } from "@/lib/utils";

interface CostTrackerProps {
  costUsd: number;
  tokensIn: number;
  tokensOut: number;
  turns: number;
  isComplete: boolean;
  budgetUsd?: number;
}

export function CostTracker({ costUsd, tokensIn, tokensOut, turns, isComplete, budgetUsd }: CostTrackerProps) {
  const hasBudget = budgetUsd != null && budgetUsd > 0;
  const ratio = hasBudget ? Math.min(costUsd / budgetUsd, 1) : 0;
  const pct = Math.round(ratio * 100);

  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center gap-6">
          <div>
            <span className="text-xs text-muted-foreground">Cost</span>
            <p className={`text-lg font-bold font-mono ${hasBudget ? getCostColor(costUsd, budgetUsd) : ""}`}>
              {formatCost(costUsd)}
            </p>
          </div>
          <div>
            <span className="text-xs text-muted-foreground">Tokens In</span>
            <p className="text-sm font-mono">{tokensIn.toLocaleString()}</p>
          </div>
          <div>
            <span className="text-xs text-muted-foreground">Tokens Out</span>
            <p className="text-sm font-mono">{tokensOut.toLocaleString()}</p>
          </div>
          <div>
            <span className="text-xs text-muted-foreground">Turns</span>
            <p className="text-sm font-mono">{turns}</p>
          </div>
          <div className="ml-auto">
            {isComplete ? (
              <span className="text-xs text-muted-foreground bg-muted px-2 py-1 rounded">Complete</span>
            ) : (
              <span className="text-xs text-success bg-success/10 px-2 py-1 rounded animate-pulse">Live</span>
            )}
          </div>
        </div>

        {hasBudget && (
          <div className="space-y-1">
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">
                Budget: {formatCost(costUsd)} / {formatCost(budgetUsd)}
              </span>
              <span className={getCostColor(costUsd, budgetUsd)}>{pct}% used</span>
            </div>
            <div className="h-2 w-full bg-muted rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-500 ${getCostBarColor(costUsd, budgetUsd)}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
