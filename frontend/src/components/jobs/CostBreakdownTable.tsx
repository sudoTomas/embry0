import { Card, CardContent } from "@/components/ui/Card";
import { formatCost, formatTokens } from "@/lib/utils";
import type { CostBreakdownEntry } from "@/lib/types";

/**
 * Per-(agent, model) cost + token rollup for a job, from GET /jobs/{id}
 * cost_breakdown (traces aggregation). EMB-35: token and cache columns make
 * per-phase spend and cache effectiveness visible at a glance.
 */
export function CostBreakdownTable({ breakdown }: { breakdown: CostBreakdownEntry[] }) {
  if (breakdown.length === 0) return null;

  return (
    <Card>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Agent</th>
                <th scope="col">Model</th>
                <th scope="col" className="text-right">Runs</th>
                <th scope="col" className="text-right">Cost</th>
                <th scope="col" className="text-right">In</th>
                <th scope="col" className="text-right">Out</th>
                <th scope="col" className="text-right">Cache read</th>
                <th scope="col" className="text-right">Cache write</th>
              </tr>
            </thead>
            <tbody>
              {breakdown.map((entry) => (
                <tr
                  key={`${entry.agent_type}-${entry.model}`}
                >
                  <td className="font-mono text-xs">{entry.agent_type}</td>
                  <td className="font-mono text-xs">{entry.model}</td>
                  <td className="text-right tabular-nums">{entry.runs}</td>
                  <td className="text-right tabular-nums">
                    {formatCost(entry.cost_usd)}
                  </td>
                  <td className="text-right tabular-nums">
                    {formatTokens(entry.input_tokens ?? 0)}
                  </td>
                  <td className="text-right tabular-nums">
                    {formatTokens(entry.output_tokens ?? 0)}
                  </td>
                  <td className="text-right tabular-nums">
                    {formatTokens(entry.cache_read_tokens ?? 0)}
                  </td>
                  <td className="text-right tabular-nums">
                    {formatTokens(entry.cache_creation_tokens ?? 0)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
