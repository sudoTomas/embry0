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
    <div className="athanor-card">
      <div className="px-6 pt-5 pb-2">
        <h2 className="text-lg font-semibold text-white">By Tier</h2>
      </div>
      <div className="px-6 pb-5">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/[0.06] text-white/40">
              <th scope="col" className="text-left py-2 font-medium">Tier</th>
              <th scope="col" className="text-right py-2 font-medium">Success</th>
              <th scope="col" className="text-right py-2 font-medium">Avg Attempts</th>
              <th scope="col" className="text-right py-2 font-medium">Avg Cost</th>
              <th scope="col" className="text-right py-2 font-medium">Total Cost</th>
            </tr>
          </thead>
          <tbody>
            {TIERS.map((tier) => (
              <tr key={tier} className="border-b border-white/[0.04] hover:bg-cyan-500/[0.02] transition-colors">
                <td className={`py-2 font-medium capitalize ${TIER_COLORS[tier]}`}>{tier}</td>
                <td className="text-right py-2">{formatPercent(successRateByTier[tier] ?? 0)}</td>
                <td className="text-right py-2">{(avgAttemptsByTier[tier] ?? 0).toFixed(1)}</td>
                <td className="text-right py-2">{formatCost(avgCostPerTier[tier] ?? 0)}</td>
                <td className="text-right py-2">{formatCost(costByTier[tier] ?? 0)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
