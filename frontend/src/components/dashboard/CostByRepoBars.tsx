import { Link } from "react-router";
import type { RepoCost } from "@/lib/types/stats";
import { formatCost } from "@/lib/utils";

interface CostByRepoBarsProps {
  costByRepo: RepoCost[];
  totalCost: number;
}

/**
 * Per-repo cost breakdown — companion's "per-project" panel, mapped onto
 * embry0's repo dimension. Shows up to 8 repos sorted by cost descending.
 * Each bar's width is the repo's cost as a fraction of the largest repo
 * (not of the total) so smaller repos remain visible.
 */
export function CostByRepoBars({ costByRepo, totalCost }: CostByRepoBarsProps) {
  if (!costByRepo || costByRepo.length === 0) {
    return (
      <div className="athanor-card px-5 py-4">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-white">Cost by Repo</h2>
          <span className="text-[11px] text-white/40 tabular-nums">{formatCost(totalCost)} total</span>
        </div>
        <p className="text-xs text-white/30">No repo activity yet.</p>
      </div>
    );
  }

  const maxCost = Math.max(...costByRepo.map((r) => r.cost_usd), 0.01);

  return (
    <div className="athanor-card px-5 py-4 animate-fade-up" style={{ animationDelay: "300ms" }}>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-white">Cost by Repo</h2>
        <span className="text-[11px] text-white/40 tabular-nums">
          {formatCost(totalCost)} total
        </span>
      </div>
      <ul className="space-y-2">
        {costByRepo.map((r) => {
          const pct = (r.cost_usd / maxCost) * 100;
          return (
            <li key={r.repo} className="grid grid-cols-[1fr_2fr_auto] items-center gap-3 text-xs">
              <Link
                to={`/issues?repo=${encodeURIComponent(r.repo)}`}
                className="font-mono text-white/70 truncate hover:text-white transition-colors"
                title={r.repo}
              >
                {r.repo}
              </Link>
              <div className="h-2 w-full rounded-full bg-white/[0.04] overflow-hidden">
                <div
                  className="h-full rounded-full bg-gradient-to-r from-cyan-500/70 to-primary/70 transition-all"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className="font-mono tabular-nums text-white/80">
                {formatCost(r.cost_usd)}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
