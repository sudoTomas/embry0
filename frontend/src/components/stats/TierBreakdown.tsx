import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { TIER_COLORS } from "@/lib/constants";
import { formatCost, formatPercent } from "@/lib/utils";

interface TierBreakdownProps {
  costByTier: Record<string, number>;
  successRateByTier: Record<string, number>;
  avgAttemptsByTier: Record<string, number>;
  avgCostPerTier: Record<string, number>;
}

const TIERS = ["routine", "standard", "complex"];

export function TierBreakdown({
  costByTier,
  successRateByTier,
  avgAttemptsByTier,
  avgCostPerTier,
}: TierBreakdownProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">By Tier</CardTitle>
      </CardHeader>
      <CardContent>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <th scope="col" className="text-left py-2">Tier</th>
              <th scope="col" className="text-right py-2">Success</th>
              <th scope="col" className="text-right py-2">Avg Attempts</th>
              <th scope="col" className="text-right py-2">Avg Cost</th>
              <th scope="col" className="text-right py-2">Total Cost</th>
            </tr>
          </thead>
          <tbody>
            {TIERS.map((tier) => (
              <tr key={tier} className="border-b border-border/50">
                <td className={`py-2 font-medium capitalize ${TIER_COLORS[tier]}`}>{tier}</td>
                <td className="text-right py-2">{formatPercent(successRateByTier[tier] ?? 0)}</td>
                <td className="text-right py-2">{(avgAttemptsByTier[tier] ?? 0).toFixed(1)}</td>
                <td className="text-right py-2">{formatCost(avgCostPerTier[tier] ?? 0)}</td>
                <td className="text-right py-2">{formatCost(costByTier[tier] ?? 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
