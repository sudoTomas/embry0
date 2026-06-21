import { useQuery } from "@tanstack/react-query";
import type { JSX } from "react";
import {
  fetchCosts,
  fetchHardware,
  fetchMemories,
  fetchReviewStats,
  fetchRoutingStats,
  type AgentCostsSummary,
  type AgentHardware,
  type AgentMemory,
  type AgentReviewStats,
  type AgentRoutingStats,
} from "@/api/agent";
import { CompactStatCard } from "@/components/stats/CompactStatCard";
import { StatCard } from "@/components/stats/StatCard";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { formatCost } from "@/lib/utils";

// Insights surface for Phase 4: cost (total + by project + top tasks),
// routing-stats, review-stats, hardware, memories. Each panel renders directly
// from the agent's response shape — the agent (`/agent/*`) is the source of
// truth, and any reshape should happen there.

const REFETCH_INTERVAL_MS = 30_000;

export function InsightsPage(): JSX.Element {
  const costs = useQuery({
    queryKey: ["agent", "costs"],
    queryFn: fetchCosts,
    refetchInterval: REFETCH_INTERVAL_MS,
  });
  const routing = useQuery({
    queryKey: ["agent", "routing-stats"],
    queryFn: fetchRoutingStats,
    refetchInterval: REFETCH_INTERVAL_MS,
  });
  const review = useQuery({
    queryKey: ["agent", "review-stats"],
    queryFn: fetchReviewStats,
    refetchInterval: REFETCH_INTERVAL_MS,
  });
  const hardware = useQuery({
    queryKey: ["agent", "hardware"],
    queryFn: fetchHardware,
    refetchInterval: REFETCH_INTERVAL_MS,
  });
  const memories = useQuery({
    queryKey: ["agent", "memories"],
    queryFn: fetchMemories,
    refetchInterval: REFETCH_INTERVAL_MS,
  });

  return (
    <div className="space-y-6 p-6">
      <header>
        <h1 className="text-2xl font-bold">Insights</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Cost, routing, reviews, hardware, and memories — pulled live from the
          agent.
        </p>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <CostPanel data={costs.data} isLoading={costs.isLoading} />
        <RoutingPanel data={routing.data} isLoading={routing.isLoading} />
        <ReviewPanel data={review.data} isLoading={review.isLoading} />
        <HardwarePanel data={hardware.data} isLoading={hardware.isLoading} />
      </div>

      <MemoriesPanel data={memories.data} isLoading={memories.isLoading} />
    </div>
  );
}

function CostPanel({
  data,
  isLoading,
}: {
  data: AgentCostsSummary | undefined;
  isLoading: boolean;
}): JSX.Element {
  const total = data?.total_usd ?? 0;
  const projects = Object.entries(data?.by_project ?? {});
  const topTasks = data?.top_tasks ?? [];
  const maxProject = Math.max(...projects.map(([, v]) => v), 0.01);
  const maxTask = Math.max(...topTasks.map((t) => t.usd), 0.01);

  return (
    <Card data-testid="insights-cost">
      <CardHeader>
        <CardTitle className="text-lg">Cost</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <StatCard title="Total Spend" value={formatCost(total)} color="#22d3ee" />

        <section>
          <p className="text-[11px] font-medium text-white/40 uppercase tracking-wider mb-2">
            By project
          </p>
          {isLoading ? (
            <p className="text-xs text-white/30">Loading...</p>
          ) : projects.length === 0 ? (
            <p className="text-xs text-white/30">No project spend recorded.</p>
          ) : (
            <ul className="space-y-2">
              {projects.map(([slug, usd]) => {
                const pct = (usd / maxProject) * 100;
                return (
                  <li
                    key={slug}
                    data-testid={`cost-project-${slug}`}
                    className="text-sm"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-white/80 truncate">
                        {slug}
                      </span>
                      <span className="font-mono text-white/60">
                        {formatCost(usd)}
                      </span>
                    </div>
                    <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden mt-1">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section>
          <p className="text-[11px] font-medium text-white/40 uppercase tracking-wider mb-2">
            Top tasks
          </p>
          {isLoading ? (
            <p className="text-xs text-white/30">Loading...</p>
          ) : topTasks.length === 0 ? (
            <p className="text-xs text-white/30">No task spend recorded.</p>
          ) : (
            <ul className="space-y-2">
              {topTasks.map((t) => {
                const pct = (t.usd / maxTask) * 100;
                return (
                  <li
                    key={t.id}
                    data-testid={`cost-task-${t.id}`}
                    className="text-sm"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-white/80 truncate">
                        {t.id}
                      </span>
                      <span className="font-mono text-white/60">
                        {formatCost(t.usd)}
                      </span>
                    </div>
                    <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden mt-1">
                      <div
                        className="h-full rounded-full bg-warning"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </CardContent>
    </Card>
  );
}

function RoutingPanel({
  data,
  isLoading,
}: {
  data: AgentRoutingStats | undefined;
  isLoading: boolean;
}): JSX.Element {
  const rows = Object.entries(data?.by_model ?? {});
  const max = Math.max(...rows.map(([, v]) => v), 1);

  return (
    <Card data-testid="insights-routing-stats">
      <CardHeader>
        <CardTitle className="text-lg">Routing</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <p className="text-xs text-white/30">Loading...</p>
        ) : rows.length === 0 ? (
          <p className="text-xs text-white/30">No routing data yet.</p>
        ) : (
          <ul className="space-y-2">
            {rows.map(([model, count]) => {
              const pct = (count / max) * 100;
              return (
                <li
                  key={model}
                  data-testid={`routing-row-${model}`}
                  className="text-sm"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-white/80 truncate">
                      {model}
                    </span>
                    <span className="font-mono text-white/60 tabular-nums">
                      {count}
                    </span>
                  </div>
                  <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden mt-1">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ReviewPanel({
  data,
  isLoading,
}: {
  data: AgentReviewStats | undefined;
  isLoading: boolean;
}): JSX.Element {
  return (
    <Card data-testid="insights-review-stats">
      <CardHeader>
        <CardTitle className="text-lg">Reviews</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <p className="text-xs text-white/30">Loading...</p>
        ) : (
          <div className="grid grid-cols-3 gap-2">
            <div data-testid="review-pass">
              <CompactStatCard
                title="Pass"
                value={String(data.pass)}
                color="#22c55e"
              />
            </div>
            <div data-testid="review-fail">
              <CompactStatCard
                title="Fail"
                value={String(data.fail)}
                color="#ef4444"
              />
            </div>
            {data.warn !== undefined && (
              <div data-testid="review-warn">
                <CompactStatCard
                  title="Warn"
                  value={String(data.warn)}
                  color="#f59e0b"
                />
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function HardwarePanel({
  data,
  isLoading,
}: {
  data: AgentHardware | undefined;
  isLoading: boolean;
}): JSX.Element {
  return (
    <Card data-testid="insights-hardware">
      <CardHeader>
        <CardTitle className="text-lg">Hardware</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {isLoading || !data ? (
          <p className="text-xs text-white/30">Loading...</p>
        ) : (
          <>
            <div className="text-sm">
              <span className="text-white/40">Host: </span>
              <span className="font-mono text-white/80">{data.host}</span>
            </div>
            {(data.cpu_pct !== undefined || data.mem_pct !== undefined) && (
              <div className="grid grid-cols-2 gap-3 text-sm">
                {data.cpu_pct !== undefined && (
                  <div>
                    <p className="text-[11px] text-white/40 uppercase tracking-wider">
                      CPU
                    </p>
                    <p className="font-mono tabular-nums text-white">
                      {data.cpu_pct}%
                    </p>
                  </div>
                )}
                {data.mem_pct !== undefined && (
                  <div>
                    <p className="text-[11px] text-white/40 uppercase tracking-wider">
                      Memory
                    </p>
                    <p className="font-mono tabular-nums text-white">
                      {data.mem_pct}%
                    </p>
                  </div>
                )}
              </div>
            )}
            {data.gpus && data.gpus.length > 0 && (
              <div>
                <p className="text-[11px] font-medium text-white/40 uppercase tracking-wider mb-2">
                  GPUs
                </p>
                <ul className="space-y-1">
                  {data.gpus.map((gpu, idx) => (
                    <li
                      key={`${gpu.name}-${idx}`}
                      data-testid={`hardware-gpu-${idx}`}
                      className="flex items-center justify-between text-sm"
                    >
                      <span className="font-mono text-white/80 truncate">
                        {gpu.name}
                      </span>
                      {gpu.mem_used_mb !== undefined && (
                        <span className="font-mono text-white/60 tabular-nums">
                          {gpu.mem_used_mb} MB
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function MemoriesPanel({
  data,
  isLoading,
}: {
  data: AgentMemory[] | undefined;
  isLoading: boolean;
}): JSX.Element {
  return (
    <Card data-testid="insights-memories">
      <CardHeader>
        <CardTitle className="text-lg">Memories</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading || !data ? (
          <p className="text-xs text-white/30">Loading...</p>
        ) : data.length === 0 ? (
          <p className="text-xs text-white/30">No memories yet.</p>
        ) : (
          <ul className="divide-y divide-white/[0.06]">
            {data.map((m) => (
              <li
                key={m.id}
                data-testid={`memory-row-${m.id}`}
                className="py-2"
              >
                {m.scope && (
                  <span className="inline-block text-[10px] font-mono uppercase tracking-wider text-white/40 mr-2">
                    {m.scope}
                  </span>
                )}
                {m.body && (
                  <span className="text-sm text-white/80">{m.body}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
