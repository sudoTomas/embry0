/**
 * Phase 5E: dashboard view of per-repo cache hit/miss aggregates.
 *
 * Reads the rollup persisted by qa_app_results.cache_analytics_window
 * over the last `windowDays` days and renders three sections:
 *   - Header  — repo · window-days · run/sub-task counters
 *   - Layers  — three hit-ratio bars (prebaked image, shared volume,
 *               turbo remote) with hits/misses labels
 *   - Cold offenders — apps whose aggregate hit_ratio fell below 0.25
 *
 * The bar palette mirrors the badge convention used elsewhere in the
 * dashboard: emerald = healthy hit, slate = miss runway. Bars never
 * overflow even on hit_ratio=1 because we clamp the width style.
 */
import { useCacheAnalytics } from "@/hooks/useQaDashboard";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import type { CacheLayerStats } from "@/lib/types";

interface Props {
  repo: string;
  windowDays?: number;
}

const LAYER_LABELS: Record<CacheLayerStats["layer"], string> = {
  prebaked_image: "Prebaked image",
  shared_volume: "Shared volume",
  turbo_remote: "Turbo remote",
};

function pct(ratio: number): string {
  // Clamp into [0, 100] defensively — backend already enforces but
  // the display string should never read "101%" if a future bug slips.
  const clamped = Math.max(0, Math.min(1, ratio));
  return `${Math.round(clamped * 100)}%`;
}

function HitRatioBar({ stats }: { stats: CacheLayerStats }) {
  const total = stats.hits + stats.misses;
  const widthPct = Math.max(0, Math.min(100, stats.hit_ratio * 100));
  const label = `${LAYER_LABELS[stats.layer]}: ${stats.hits}/${total} (${pct(stats.hit_ratio)})`;
  return (
    <div className="space-y-1" data-testid={`cache-layer-${stats.layer}`}>
      <div className="flex items-baseline justify-between text-sm">
        <span className="text-white/80">{LAYER_LABELS[stats.layer]}</span>
        <span className="font-mono text-white/60">
          {stats.hits}/{total} ({pct(stats.hit_ratio)})
        </span>
      </div>
      <div
        className="h-2 w-full overflow-hidden rounded-full bg-white/5"
        role="progressbar"
        aria-label={label}
        aria-valuenow={Math.round(stats.hit_ratio * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-full rounded-full bg-emerald-500/70 transition-[width]"
          style={{ width: `${widthPct}%` }}
        />
      </div>
    </div>
  );
}

export function CacheAnalyticsView({ repo, windowDays = 30 }: Props) {
  const { data, isLoading, isError, refetch } = useCacheAnalytics(repo, windowDays);

  if (isError) {
    return (
      <PageError
        message="Failed to load cache analytics"
        onRetry={() => refetch()}
      />
    );
  }
  if (isLoading || !data) return <TableSkeleton />;

  return (
    <div className="space-y-4 p-6" data-testid="cache-analytics-view">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold font-mono">{data.repo}</h1>
        <p className="text-sm text-white/60">
          Cache analytics · last {data.window_days} days · {data.total_runs} runs
          · {data.total_subtasks} sub-tasks
        </p>
      </header>

      <section
        className="space-y-3 rounded-md border bg-card p-4"
        data-testid="cache-layers-section"
      >
        <h2 className="text-sm font-semibold uppercase tracking-wide text-white/70">
          Hit ratios
        </h2>
        {data.layers.map((layer) => (
          <HitRatioBar key={layer.layer} stats={layer} />
        ))}
      </section>

      <section
        className="rounded-md border bg-card p-4"
        data-testid="cold-cache-section"
      >
        <h2 className="text-sm font-semibold uppercase tracking-wide text-white/70">
          Apps with low hit rates ({data.cold_cache_apps.length})
        </h2>
        {data.cold_cache_apps.length === 0 ? (
          <p className="mt-2 text-sm text-white/40">None.</p>
        ) : (
          <ul className="mt-2 space-y-1 text-sm">
            {data.cold_cache_apps.map((app) => (
              <li
                key={app}
                className="break-words rounded-sm bg-white/5 px-2 py-1 font-mono text-white/80"
              >
                {app}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
