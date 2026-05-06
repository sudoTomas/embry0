/**
 * Phase 5F: dashboard view of per-repo flake counts as an apps × days heatmap.
 *
 * A "flake" is an app whose status flipped between consecutive runs on the
 * SAME workspace head_sha — surfaces tests / boots / e2e checks that are
 * not deterministic about a fixed code state. The heatmap renders one row
 * per app (sorted by flake_score desc — worst offenders first) and one
 * column per day in the window (oldest left → today right). Cells with
 * zero flakes are a subtle slate; cells with > 0 flakes glow red, deeper
 * with intensity.
 *
 * Palette mirrors Phase 5A's "opacity-on-white" learning: cell colors are
 * `bg-rose-500/<alpha>` so they compose cleanly on the dashboard's dark
 * background without bleeding into adjacent UI.
 */
import { useFlake } from "@/hooks/useQaDashboard";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import type { FlakeRow } from "@/lib/types";

interface Props {
  repo: string;
  windowDays?: number;
}

/**
 * Map a flake count to a Tailwind opacity bucket on `bg-rose-500/<alpha>`.
 *
 * Buckets, not a continuous scale, because Tailwind purges classes that
 * aren't statically present at build time — interpolating an arbitrary
 * alpha value would render as no class at all. The 0-cell uses a subtle
 * slate so the grid remains visible even when nothing flaked.
 */
function cellClass(flakes: number): string {
  if (flakes <= 0) return "bg-white/5";
  if (flakes === 1) return "bg-rose-500/30";
  if (flakes === 2) return "bg-rose-500/50";
  if (flakes <= 4) return "bg-rose-500/70";
  return "bg-rose-500/90";
}

function HeatmapRow({ row }: { row: FlakeRow }) {
  const totalFlakes = row.flake_count;
  return (
    <div
      className="grid grid-cols-[10rem_1fr_4rem] items-center gap-2 py-1"
      data-testid={`flake-row-${row.app_name}`}
    >
      <span
        className="truncate font-mono text-sm text-white/80"
        title={row.app_name}
      >
        {row.app_name}
      </span>
      <div
        className="grid gap-1"
        style={{
          gridTemplateColumns: `repeat(${row.daily.length}, minmax(0, 1fr))`,
        }}
      >
        {row.daily.map((d) => {
          const label = `${row.app_name} on ${d.date}: ${d.flakes} flake${d.flakes === 1 ? "" : "s"}`;
          return (
            <div
              key={d.date}
              role="gridcell"
              data-testid={`flake-cell-${row.app_name}-${d.date}`}
              data-flakes={d.flakes}
              // a11y: color (rose-500 alpha bucket) is paired with a numeric
              // glyph for buckets >= 3 so color-blind users get a non-color
              // intensity signal. aria-label echoes the title so screen
              // readers announce the data point even when the title tooltip
              // is unavailable (some readers ignore title on non-interactive
              // elements).
              className={`flex h-6 items-center justify-center rounded-sm ${cellClass(d.flakes)}`}
              title={label}
              aria-label={label}
            >
              {d.flakes >= 3 && (
                <span className="text-[10px] font-mono leading-none text-white">
                  {d.flakes}
                </span>
              )}
            </div>
          );
        })}
      </div>
      <span
        className="text-right font-mono text-xs text-white/60"
        title={`${totalFlakes} flake events / ${row.total_runs} runs`}
      >
        {totalFlakes}/{row.total_runs}
      </span>
    </div>
  );
}

export function FlakeHeatmap({ repo, windowDays = 7 }: Props) {
  const { data, isLoading, isError, refetch } = useFlake(repo, windowDays);

  if (isError) {
    return (
      <PageError
        message="Failed to load flake heatmap"
        onRetry={() => refetch()}
      />
    );
  }
  if (isLoading || !data) return <TableSkeleton />;

  const apps = data.apps;
  const totalFlakes = apps.reduce((acc, r) => acc + r.flake_count, 0);

  // Pull the day-key list off the first row so the column header matches the
  // grid below pixel-for-pixel. Empty-state has no rows, so we fall back to
  // an empty array and skip the header (the empty-state copy explains).
  const dayKeys = apps[0]?.daily.map((d) => d.date) ?? [];

  return (
    <div className="space-y-4 p-6" data-testid="flake-heatmap">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold font-mono">{data.repo}</h1>
        <p className="text-sm text-white/60">
          Flake heatmap · {apps.length} {apps.length === 1 ? "app" : "apps"} ·{" "}
          {totalFlakes} total {totalFlakes === 1 ? "flake" : "flakes"} · last{" "}
          {data.window_days} days
        </p>
      </header>

      {apps.length === 0 ? (
        <section
          className="rounded-md border bg-card p-4 text-sm text-white/40"
          data-testid="flake-heatmap-empty"
        >
          No QA runs in the last {data.window_days} days, or no run captured a
          head_sha (legacy rows). The heatmap fills in once two consecutive runs
          on the same workspace head exist.
        </section>
      ) : (
        <section
          className="space-y-1 rounded-md border bg-card p-4"
          data-testid="flake-heatmap-grid"
        >
          {/* Day-key header — short MM-DD labels keep the grid compact. */}
          <div
            className="grid grid-cols-[10rem_1fr_4rem] items-center gap-2 pb-2 text-[10px] uppercase tracking-wide text-white/40"
            data-testid="flake-heatmap-header"
          >
            <span>App</span>
            <div
              className="grid gap-1 font-mono"
              style={{
                gridTemplateColumns: `repeat(${dayKeys.length}, minmax(0, 1fr))`,
              }}
            >
              {dayKeys.map((d) => (
                <span key={d} className="truncate text-center" title={d}>
                  {d.slice(5) /* MM-DD */}
                </span>
              ))}
            </div>
            <span className="text-right">Flakes/Runs</span>
          </div>

          {apps.map((row) => (
            <HeatmapRow key={row.app_name} row={row} />
          ))}
        </section>
      )}
    </div>
  );
}
