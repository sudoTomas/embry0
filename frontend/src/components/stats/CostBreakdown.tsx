import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { formatCost } from "@/lib/utils";

/** Background colors matching tier semantics (green/amber/red). */
const TIER_BAR_COLORS: Record<string, string> = {
  routine: "bg-success",
  standard: "bg-warning",
  complex: "bg-destructive",
};

const TIER_LABEL_COLORS: Record<string, string> = {
  routine: "text-success",
  standard: "text-warning",
  complex: "text-destructive",
};

interface CostBreakdownProps {
  costByTier: Record<string, number>;
  dailyCost: number;
  monthlyCost: number;
}

export function CostBreakdown({ costByTier, dailyCost, monthlyCost }: CostBreakdownProps) {
  const tiers = ["routine", "standard", "complex"];
  const maxCost = Math.max(...tiers.map((t) => costByTier[t] ?? 0), 0.01);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">Cost Breakdown</CardTitle>
      </CardHeader>
      <CardContent>
        {/* Horizontal bar chart */}
        <div className="space-y-3">
          {tiers.map((tier) => {
            const cost = costByTier[tier] ?? 0;
            const pct = (cost / maxCost) * 100;
            return (
              <div key={tier}>
                <div className="flex items-center justify-between text-sm mb-1">
                  <span className={`capitalize font-medium ${TIER_LABEL_COLORS[tier] ?? ""}`}>
                    {tier}
                  </span>
                  <span className="font-mono text-muted-foreground">{formatCost(cost)}</span>
                </div>
                <div className="h-3 w-full rounded-full bg-muted overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${TIER_BAR_COLORS[tier] ?? "bg-primary"}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>

        {/* Daily / Monthly summary */}
        <div className="mt-4 pt-4 border-t border-border grid grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-muted-foreground">Daily</p>
            <p className="text-xl font-bold">{formatCost(dailyCost)}</p>
          </div>
          <div>
            <p className="text-muted-foreground">Monthly</p>
            <p className="text-xl font-bold">{formatCost(monthlyCost)}</p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
