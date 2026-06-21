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

// Insights surface for Phase 4: cost (per-provider tokens + spend), routing
// stats (by model / by phase), review stats (dual-review agreement), hardware,
// and memories. Every panel renders the agent's real response shape — the
// agent (`/agent/*`) is the source of truth. No panel renders an object or
// array as a JSX child; each maps to scalar fields.

const REFETCH_INTERVAL_MS = 30_000;

// `ollama_models` arrives as a JSON-encoded string. Parse it defensively:
// a malformed payload or a non-array must never throw during render.
interface OllamaModel {
  name?: string;
  model?: string;
  size?: number;
}

function parseOllamaModels(raw: string | undefined): OllamaModel[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (m): m is OllamaModel => typeof m === "object" && m !== null,
    );
  } catch {
    return [];
  }
}

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

function formatTokens(n: number): string {
  return n.toLocaleString("en-US");
}

function ProviderRow({
  name,
  cost,
  color,
}: {
  name: string;
  cost: { real_cost_usd?: number; notional_cost_usd?: number; tokens_in: number; tokens_out: number; subscription?: string };
  color: string;
}): JSX.Element {
  const usd = cost.real_cost_usd ?? cost.notional_cost_usd ?? 0;
  return (
    <li data-testid={`cost-provider-${name}`} className="text-sm">
      <div className="flex items-center justify-between">
        <span className="font-mono text-white/80 truncate">
          {name}
          {cost.subscription && (
            <span className="ml-2 text-[10px] uppercase tracking-wider text-white/40">
              {cost.subscription}
            </span>
          )}
        </span>
        <span className="font-mono" style={{ color }}>
          {formatCost(usd)}
        </span>
      </div>
      <div className="mt-1 flex gap-4 text-[11px] text-white/40">
        <span>in {formatTokens(cost.tokens_in)}</span>
        <span>out {formatTokens(cost.tokens_out)}</span>
      </div>
    </li>
  );
}

function CostPanel({
  data,
  isLoading,
}: {
  data: AgentCostsSummary | undefined;
  isLoading: boolean;
}): JSX.Element {
  const grok = data?.grok;
  const claude = data?.claude;
  const dailyUsage = data?.daily_usage ?? [];
  const total =
    (grok?.real_cost_usd ?? grok?.notional_cost_usd ?? 0) +
    (claude?.real_cost_usd ?? claude?.notional_cost_usd ?? 0);
  const hasProviders = Boolean(grok || claude);

  return (
    <Card data-testid="insights-cost">
      <CardHeader>
        <CardTitle className="text-lg">Cost</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <StatCard title="Total Spend" value={formatCost(total)} color="#22d3ee" />

        <section>
          <p className="text-[11px] font-medium text-white/40 uppercase tracking-wider mb-2">
            By provider
          </p>
          {isLoading ? (
            <p className="text-xs text-white/30">Loading...</p>
          ) : !hasProviders ? (
            <p className="text-xs text-white/30">No provider spend recorded.</p>
          ) : (
            <ul className="space-y-3">
              {claude && (
                <ProviderRow name="claude" cost={claude} color="#d97757" />
              )}
              {grok && <ProviderRow name="grok" cost={grok} color="#a1a1aa" />}
            </ul>
          )}
        </section>

        <section>
          <p className="text-[11px] font-medium text-white/40 uppercase tracking-wider mb-2">
            Daily usage
          </p>
          {isLoading ? (
            <p className="text-xs text-white/30">Loading...</p>
          ) : dailyUsage.length === 0 ? (
            <p className="text-xs text-white/30">No daily usage recorded.</p>
          ) : (
            <ul className="space-y-2">
              {dailyUsage.map((d) => (
                <li
                  key={d.day}
                  data-testid={`cost-day-${d.day}`}
                  className="flex items-center justify-between text-sm"
                >
                  <span className="font-mono text-white/80">{d.day}</span>
                  <span className="font-mono text-white/50 tabular-nums">
                    {d.tasks_completed} tasks · {formatTokens(d.tokens_out)} out
                  </span>
                </li>
              ))}
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
  const byModel = data?.by_model ?? [];
  const byPhase = data?.by_phase ?? [];
  const maxModel = Math.max(...byModel.map((r) => r.count), 1);
  const maxPhase = Math.max(...byPhase.map((r) => r.count), 1);

  return (
    <Card data-testid="insights-routing-stats">
      <CardHeader>
        <CardTitle className="text-lg">Routing</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <section>
          <p className="text-[11px] font-medium text-white/40 uppercase tracking-wider mb-2">
            By model
          </p>
          {isLoading ? (
            <p className="text-xs text-white/30">Loading...</p>
          ) : byModel.length === 0 ? (
            <p className="text-xs text-white/30">No routing data yet.</p>
          ) : (
            <ul className="space-y-2">
              {byModel.map((row) => {
                const pct = (row.count / maxModel) * 100;
                return (
                  <li
                    key={row.routed_model}
                    data-testid={`routing-row-${row.routed_model}`}
                    className="text-sm"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-white/80 truncate">
                        {row.routed_model}
                      </span>
                      <span className="font-mono text-white/60 tabular-nums">
                        {row.count}
                        {row.success_rate !== undefined && (
                          <span className="ml-2 text-white/40">
                            {Math.round(row.success_rate * 100)}%
                          </span>
                        )}
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
            By phase
          </p>
          {isLoading ? (
            <p className="text-xs text-white/30">Loading...</p>
          ) : byPhase.length === 0 ? (
            <p className="text-xs text-white/30">No phase data yet.</p>
          ) : (
            <ul className="space-y-2">
              {byPhase.map((row) => {
                const pct = (row.count / maxPhase) * 100;
                return (
                  <li
                    key={row.phase}
                    data-testid={`routing-phase-${row.phase}`}
                    className="text-sm"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-white/80 truncate">
                        {row.phase}
                      </span>
                      <span className="font-mono text-white/60 tabular-nums">
                        {row.count}
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

function ReviewPanel({
  data,
  isLoading,
}: {
  data: AgentReviewStats | undefined;
  isLoading: boolean;
}): JSX.Element {
  // agreement_rate is a string ("N/A") OR a number — stringify for display.
  const agreement =
    data === undefined
      ? "—"
      : typeof data.agreement_rate === "number"
        ? `${Math.round(data.agreement_rate * 100)}%`
        : data.agreement_rate;

  return (
    <Card data-testid="insights-review-stats">
      <CardHeader>
        <CardTitle className="text-lg">Reviews</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading || !data ? (
          <p className="text-xs text-white/30">Loading...</p>
        ) : (
          <>
            <div className="grid grid-cols-3 gap-2">
              <div data-testid="review-agreement">
                <CompactStatCard
                  title="Agreement"
                  value={String(agreement)}
                  color="#22c55e"
                />
              </div>
              <div data-testid="review-dual">
                <CompactStatCard
                  title="Dual reviews"
                  value={String(data.total_dual_reviews)}
                  color="#22d3ee"
                />
              </div>
              <div data-testid="review-agreed">
                <CompactStatCard
                  title="Agreed"
                  value={String(data.agreed)}
                  color="#a1a1aa"
                />
              </div>
            </div>

            <section>
              <p className="text-[11px] font-medium text-white/40 uppercase tracking-wider mb-2">
                By type
              </p>
              {data.by_type.length === 0 ? (
                <p className="text-xs text-white/30">No reviews by type yet.</p>
              ) : (
                <ul className="space-y-1">
                  {data.by_type.map((row) => (
                    <li
                      key={row.type}
                      data-testid={`review-type-${row.type}`}
                      className="flex items-center justify-between text-sm"
                    >
                      <span className="font-mono text-white/80 truncate">
                        {row.type}
                      </span>
                      <span className="font-mono text-white/60 tabular-nums">
                        {row.count}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
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
  const models = parseOllamaModels(data?.ollama_models);

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
              <span className="font-mono text-white/80">{data.hostname}</span>
            </div>
            {(data.total_memory_gb !== undefined ||
              data.available_memory_gb !== undefined) && (
              <div className="grid grid-cols-2 gap-3 text-sm">
                {data.total_memory_gb !== undefined && (
                  <div>
                    <p className="text-[11px] text-white/40 uppercase tracking-wider">
                      Total memory
                    </p>
                    <p className="font-mono tabular-nums text-white">
                      {data.total_memory_gb} GB
                    </p>
                  </div>
                )}
                {data.available_memory_gb !== undefined && (
                  <div>
                    <p className="text-[11px] text-white/40 uppercase tracking-wider">
                      Available
                    </p>
                    <p className="font-mono tabular-nums text-white">
                      {data.available_memory_gb} GB
                    </p>
                  </div>
                )}
              </div>
            )}
            {data.gpu_info && (
              <div className="text-sm">
                <span className="text-white/40">GPU: </span>
                <span className="font-mono text-white/80">{data.gpu_info}</span>
              </div>
            )}
            {models.length > 0 && (
              <div>
                <p className="text-[11px] font-medium text-white/40 uppercase tracking-wider mb-2">
                  Ollama models
                </p>
                <ul className="space-y-1">
                  {models.map((m, idx) => {
                    const label = m.name ?? m.model ?? `model ${idx}`;
                    return (
                      <li
                        key={`${label}-${idx}`}
                        data-testid={`hardware-model-${idx}`}
                        className="flex items-center justify-between text-sm"
                      >
                        <span className="font-mono text-white/80 truncate">
                          {label}
                        </span>
                      </li>
                    );
                  })}
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
                key={String(m.id)}
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
