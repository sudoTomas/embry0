import { useStats } from "@/hooks/useStats";
import { useAgentStats } from "@/hooks/useAgentStats";
import {
  Heartbeat,
  SingleSourceTile,
  type SingleSourceQuery,
} from "@/components/vitals";

// Format helpers — kept local; they're the only translation layer between
// raw backend numbers and tile strings.
const formatCount = (n: number) => String(n);
const formatPercent = (n: number) => `${Math.round(n * 100)}%`;
const formatUsd = (n: number) => `$${n.toFixed(2)}`;

// Project a useQuery result onto the SingleSourceQuery contract with a
// per-tile value selector. Keeping the selector at the call site means
// each tile names its own source field, which makes the assay's
// source-mapping guards trivially auditable.
function selectQuery<TSource, TValue>(
  result: { data: TSource | undefined; isPending: boolean; isError: boolean },
  select: (source: TSource) => TValue,
): SingleSourceQuery<TValue> {
  return {
    isError: result.isError,
    isPending: result.isPending,
    data: result.data === undefined ? undefined : select(result.data),
  };
}

export function DashboardPage() {
  const orchestrator = useStats();
  const agent = useAgentStats();

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <SingleSourceTile
          label="Running"
          query={selectQuery(agent, (s) => s.running)}
          format={formatCount}
        />
        <SingleSourceTile
          label="Queued"
          query={selectQuery(agent, (s) => s.queued)}
          format={formatCount}
        />
        <SingleSourceTile
          label="Done"
          query={selectQuery(agent, (s) => s.done)}
          format={formatCount}
        />
        <SingleSourceTile
          label="Failed"
          query={selectQuery(agent, (s) => s.failed)}
          format={formatCount}
        />
        <SingleSourceTile
          label="QA Pass Rate"
          query={selectQuery(orchestrator, (s) => s.success_rate)}
          format={formatPercent}
        />
        <SingleSourceTile
          label="Cost Today"
          query={selectQuery(orchestrator, (s) => s.daily_cost_usd)}
          format={formatUsd}
        />
      </div>

      <div
        data-testid="heartbeat-strip"
        className="flex flex-wrap items-center gap-6"
      >
        <Heartbeat label="orchestrator live" />
        <Heartbeat label="agent live" />
      </div>
    </div>
  );
}
