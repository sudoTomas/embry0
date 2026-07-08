import { useCallback, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Plus, Table2 } from "lucide-react";
import { useJobs } from "@/hooks/useJobs";
import { useQueue } from "@/hooks/useQueue";
import { useConfig } from "@/hooks/useConfig";
import { useJobInputs } from "@/hooks/useInputs";
import { BOARD_COLUMNS, groupJobsByColumn, type BoardColumnId } from "@/lib/boardColumns";
import { BoardColumn } from "@/components/console/BoardColumn";
import { RunningCard } from "@/components/console/RunningCard";
import { NeedsYouCard } from "@/components/console/NeedsYouCard";
import { QueuedCard } from "@/components/console/QueuedCard";
import { DoneFailedCard } from "@/components/console/DoneFailedCard";
import { HeartbeatBadge } from "@/components/console/HeartbeatBadge";
import { NewJobForm } from "@/components/console/NewJobForm";
import { EmptyJobsState } from "@/components/jobs/EmptyJobsState";
import { PageError } from "@/components/PageError";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import type { JobResponse, JobStatus } from "@/lib/types";

/** Board poll cadence (spec Increment 1: card membership from useJobs at 5s;
 * the HeartbeatBadge staleness thresholds are tuned to this). */
const BOARD_POLL_MS = 5_000;
/** One page is the whole board — sandbox concurrency is single-digit and the
 * Done/Failed lanes are capped to a day, so 100 rows covers it. */
const BOARD_FETCH_LIMIT = 100;
/** Done/Failed lanes show the last ~24h only — deep history stays on JobsPage
 * (and the Runs tab, once Increment 2 lands). */
const TERMINAL_WINDOW_MS = 24 * 60 * 60 * 1000;

/** Every JobStatus, lane order — the board filters on raw status, not lane. */
const STATUS_OPTIONS: JobStatus[] = [
  "awaiting_input",
  "paused",
  "pending",
  "running",
  "completed",
  "pr_merged",
  "failed",
  "partial",
  "cancelled",
  "expired",
  "pr_closed",
];

/** When a terminal card left the pipeline — created_at is the fallback for
 * rows that never recorded a finish (e.g. cancelled before start). */
function terminalAt(job: JobResponse): number {
  return new Date(job.finished_at ?? job.created_at).getTime();
}

/** Per-card container for the Needs You lane: a component (not a loop of
 * hooks) so each blocked job owns its own useJobInputs subscription feeding
 * the inline QuestionsForm. */
function NeedsYouCardWithInputs({ job }: { job: JobResponse }) {
  const { data: inputs } = useJobInputs(job.job_id);
  return <NeedsYouCard job={job} jobInputs={inputs} />;
}

/**
 * The live console (`/console`, spec Increments 1 + 1b): a five-lane kanban
 * board of every job, derived from JobStatus — read-only lanes, no
 * drag-and-drop. Board/Runs tabs, `?status=`/`?repo=`/`?label=`/`?tab=`
 * URL-synced so dispatching sessions can hand back a watchable link.
 *
 * Polling defenses (the companion-dashboard idioms as React): react-query's
 * structural sharing keeps the jobs array identity stable when a poll returns
 * unchanged data, so the memoized cards skip re-render; per-card open/scroll
 * state lives in the cards themselves, never in polled data.
 */
export function ConsolePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") === "runs" ? "runs" : "board";
  const statusFilter = searchParams.get("status") ?? undefined;
  const repoFilter = searchParams.get("repo") ?? undefined;
  // Accepted and round-tripped today, filtering only once jobs expose labels
  // (Increment 2's `labels` backend addition) — no-op-safe until then.
  const labelFilter = searchParams.get("label") ?? undefined;
  const [showCreate, setShowCreate] = useState(false);

  const setParam = useCallback(
    (key: string, value: string | undefined) => {
      setSearchParams((prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      });
    },
    [setSearchParams],
  );

  // Status/repo filters ride the query itself (same server-side filtering as
  // JobsPage) — a filtered board renders its lanes with only matching cards.
  const jobFilters = useMemo(
    () => ({ limit: BOARD_FETCH_LIMIT, status: statusFilter, repo: repoFilter }),
    [statusFilter, repoFilter],
  );
  const { data, isLoading, isError, refetch, dataUpdatedAt } = useJobs(jobFilters, BOARD_POLL_MS);
  const { data: queue } = useQueue();
  const { data: config } = useConfig();

  const visibleJobs = useMemo(() => {
    const jobs = data?.jobs ?? [];
    if (!labelFilter) return jobs;
    return jobs.filter((job) => {
      // JobResponse doesn't expose labels yet; when it does (Increment 2),
      // this predicate starts filtering with no further change here.
      const labels = (job as JobResponse & { labels?: string[] }).labels;
      return labels == null || labels.includes(labelFilter);
    });
  }, [data, labelFilter]);

  const lanes = useMemo(() => {
    const grouped = groupJobsByColumn(visibleJobs);
    // The cutoff freezes between data changes by design — recomputing only
    // when the poll actually delivers new data is the skip-re-render defense.
    const cutoff = Date.now() - TERMINAL_WINDOW_MS;
    for (const lane of ["done", "failed"] as const) {
      grouped[lane] = grouped[lane]
        .filter((job) => terminalAt(job) >= cutoff)
        .sort((a, b) => terminalAt(b) - terminalAt(a));
    }
    // Queue order: oldest pending first, matching the executor's FIFO pickup.
    grouped.queued.sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
    return grouped;
  }, [visibleJobs]);

  const totalCards = BOARD_COLUMNS.reduce((n, col) => n + lanes[col.id].length, 0);
  const hasFilters = Boolean(statusFilter || repoFilter || labelFilter);
  // Empty *board*, not empty *filter result*: with filters active the lanes
  // render (empty) so the filter state stays visible and clearable.
  const boardEmpty = !isLoading && !isError && totalCards === 0 && !hasFilters;

  const repoOptions = useMemo(() => {
    const seen = new Set((data?.jobs ?? []).map((j) => j.repo).filter(Boolean));
    if (repoFilter) seen.add(repoFilter);
    return Array.from(seen).sort();
  }, [data, repoFilter]);

  const maxBudgetUsd = config?.max_budget_per_job_usd ?? null;

  const laneCards = (columnId: BoardColumnId) => {
    switch (columnId) {
      case "needs_you":
        return lanes.needs_you.map((job) => <NeedsYouCardWithInputs key={job.job_id} job={job} />);
      case "queued":
        return lanes.queued.map((job, idx) => (
          <QueuedCard
            key={job.job_id}
            job={job}
            position={idx + 1}
            queueDepth={queue?.pending ?? lanes.queued.length}
          />
        ));
      case "running":
        return lanes.running.map((job) => (
          <RunningCard key={job.job_id} job={job} maxBudgetUsd={maxBudgetUsd} />
        ));
      case "done":
      case "failed":
        return lanes[columnId].map((job) => <DoneFailedCard key={job.job_id} job={job} />);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold">Console</h1>
          <HeartbeatBadge lastUpdatedAt={dataUpdatedAt || null} />
        </div>
        <Button onClick={() => setShowCreate((s) => !s)}>
          <Plus className="h-4 w-4 mr-2" /> New Job
        </Button>
      </div>

      {showCreate && <NewJobForm onClose={() => setShowCreate(false)} knownRepos={repoOptions} />}

      <Tabs defaultValue="board" value={tab} onValueChange={(v) => setParam("tab", v)}>
        <TabsList>
          <TabsTrigger value="board">Board</TabsTrigger>
          <TabsTrigger value="runs" className="gap-1.5">
            Runs
            <span className="rounded bg-white/[0.06] px-1 py-0.5 text-[9px] font-mono text-white/30">
              Increment 2
            </span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="board" className="space-y-4">
          <div className="flex items-center gap-2">
            <FilterSelect
              label="Statuses"
              value={statusFilter}
              options={STATUS_OPTIONS}
              onChange={(v) => setParam("status", v)}
            />
            <FilterSelect
              label="Repos"
              value={repoFilter}
              options={repoOptions}
              onChange={(v) => setParam("repo", v)}
            />
            {labelFilter && (
              <button
                type="button"
                data-testid="label-filter-chip"
                onClick={() => setParam("label", undefined)}
                title="Clear label filter"
                className="inline-flex items-center gap-1 rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-xs font-mono text-white/50 hover:text-white/80"
              >
                label: {labelFilter} ×
              </button>
            )}
          </div>

          {isError ? (
            <PageError message="Failed to load jobs" onRetry={() => refetch()} />
          ) : boardEmpty ? (
            <EmptyJobsState variant="console" onNewJob={() => setShowCreate(true)} />
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3 xl:grid-cols-5 items-start">
              {BOARD_COLUMNS.map((column) => (
                <BoardColumn
                  key={column.id}
                  column={column}
                  count={lanes[column.id].length}
                  isLoading={isLoading}
                >
                  {laneCards(column.id)}
                </BoardColumn>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="runs">
          {/* Increment 2 placeholder — the run-comparison table (duration,
              per-agent cost split, verdicts, error codes) is deliberately not
              built yet. Deep history lives on JobsPage meanwhile. */}
          <EmptyState
            icon={Table2}
            title="Runs lands in Increment 2"
            description="Side-by-side run comparison — duration, cost split, review verdicts, error codes — ships in Increment 2. Until then, deep job history lives on the Jobs page."
          >
            <Link to="/jobs">
              <Button variant="outline" size="sm">
                Open Jobs history
              </Button>
            </Link>
          </EmptyState>
        </TabsContent>
      </Tabs>
    </div>
  );
}
