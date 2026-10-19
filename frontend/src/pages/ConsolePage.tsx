import { useCallback, useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { Plus } from "lucide-react";
import { useJobs } from "@/hooks/useJobs";
import { useQueue } from "@/hooks/useQueue";
import { useConfig } from "@/hooks/useConfig";
import { useGlide } from "@/hooks/useGlide";
import { useJobInputs, useJobInputsAnswered } from "@/hooks/useInputs";
import { hasAnswered, useFirstPaint } from "@/hooks/useFirstPaint";
import { BOARD_COLUMNS, groupJobsByColumn, type BoardColumnId } from "@/lib/boardColumns";
import { BoardColumn } from "@/components/console/BoardColumn";
import { RunningCard } from "@/components/console/RunningCard";
import { NeedsYouCard } from "@/components/console/NeedsYouCard";
import { QueuedCard } from "@/components/console/QueuedCard";
import { DoneFailedCard } from "@/components/console/DoneFailedCard";
import { HeartbeatBadge } from "@/components/console/HeartbeatBadge";
import { NewJobForm } from "@/components/console/NewJobForm";
import { GlideBoard, GlideItem } from "@/components/console/GlideBoard";
import { EmptyJobsState } from "@/components/jobs/EmptyJobsState";
import { PageError } from "@/components/PageError";
import { Button } from "@/components/ui/Button";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { ALL_JOB_STATUSES } from "@/lib/types/jobs";
import { statusLabel } from "@/lib/status";
import type { JobResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Board poll cadence (spec Increment 1: card membership from useJobs at 5s;
 * the HeartbeatBadge staleness thresholds are tuned to this). */
const BOARD_POLL_MS = 5_000;
/** One page is the whole board — sandbox concurrency is single-digit and the
 * Done/Failed lanes are capped to a day, so 100 rows covers it. */
const BOARD_FETCH_LIMIT = 100;
/** Done/Failed lanes show the last ~24h only — deep history stays on JobsPage. */
const TERMINAL_WINDOW_MS = 24 * 60 * 60 * 1000;

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
 * drag-and-drop. `?status=`/`?repo=` URL-synced so dispatching sessions can
 * hand back a watchable link.
 *
 * Polling defenses (the Companion-dashboard idioms as React): react-query's
 * structural sharing keeps the jobs array identity stable when a poll returns
 * unchanged data, so the memoized cards skip re-render; per-card open/scroll
 * state lives in the cards themselves, never in polled data.
 */
export function ConsolePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const statusFilter = searchParams.get("status") ?? undefined;
  const repoFilter = searchParams.get("repo") ?? undefined;
  const [showCreate, setShowCreate] = useState(false);
  const glide = useGlide();
  // A filter change cuts the board differently; no job moved, so it paints
  // afresh: nothing glides and no lane's count ticks.
  const paintKey = `${statusFilter ?? ""}|${repoFilter ?? ""}`;

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
  const jobs = useJobs(jobFilters, BOARD_POLL_MS);
  const { data, isLoading, isError, refetch, dataUpdatedAt } = jobs;
  const { data: queue } = useQueue();
  const { data: config } = useConfig();

  const visibleJobs = useMemo(() => data?.jobs ?? [], [data]);

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

  // The board paints once the jobs and the questions of the jobs that need
  // someone are in. Painted before, the lanes stood as skeletons, then took
  // cards of other heights, and a question grew its card under the lanes
  // below it: on a phone, where the lanes stack, that pushed them all down.
  const needsYouIds = useMemo(() => lanes.needs_you.map((job) => job.job_id), [lanes]);
  const questionsAnswered = useJobInputsAnswered(needsYouIds);
  const painted = useFirstPaint(hasAnswered(jobs) && questionsAnswered);

  // An agent's question needs a line long enough to read: on a wide screen,
  // while one waits, its lane takes twice a lane's share. The other lanes
  // keep the width their cards' header rows need (repo, id, status pill).
  // Only once painted, so no skeleton lane moves as the jobs come in.
  const roomForQuestion = painted && lanes.needs_you.length > 0;

  const totalCards = BOARD_COLUMNS.reduce((n, col) => n + lanes[col.id].length, 0);
  const hasFilters = Boolean(statusFilter || repoFilter);
  // Empty *board*, not empty *filter result*: with filters active the lanes
  // render (empty) so the filter state stays visible and clearable.
  const boardEmpty = !isLoading && !isError && totalCards === 0 && !hasFilters;

  const repoOptions = useMemo(() => {
    const seen = new Set((data?.jobs ?? []).flatMap((j) => (j.repo ? [j.repo] : [])));
    if (repoFilter) seen.add(repoFilter);
    return Array.from(seen).sort();
  }, [data, repoFilter]);

  const maxBudgetUsd = config?.max_budget_per_job_usd ?? null;

  const laneCard = (columnId: BoardColumnId, job: JobResponse, idx: number) => {
    switch (columnId) {
      case "needs_you":
        return <NeedsYouCardWithInputs job={job} />;
      case "queued":
        return (
          <QueuedCard
            job={job}
            position={idx + 1}
            queueDepth={queue?.pending ?? lanes.queued.length}
          />
        );
      case "running":
        return <RunningCard job={job} maxBudgetUsd={maxBudgetUsd} />;
      case "done":
      case "failed":
        return <DoneFailedCard job={job} />;
    }
  };

  // Each card sits in a GlideItem keyed by its job, so a poll that moves a
  // job to another lane shows the card travelling there.
  const laneCards = (columnId: BoardColumnId) =>
    lanes[columnId].map((job, idx) => (
      <GlideItem key={job.job_id} id={job.job_id} index={idx}>
        {laneCard(columnId, job, idx)}
      </GlideItem>
    ));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold">Console</h1>
          <HeartbeatBadge lastUpdatedAt={dataUpdatedAt || null} connecting={!hasAnswered(jobs)} />
        </div>
        {/* While an agent waits on someone, answering it is the page's one
            gold action, so New job steps down to the secondary tone. Until
            the jobs are in, the page does not know which it is: New job
            waits in the secondary tone, rather than lighting gold and
            going out when they show an agent waiting. */}
        <Button
          variant={data && lanes.needs_you.length === 0 ? "default" : "secondary"}
          onClick={() => setShowCreate((s) => !s)}
        >
          <Plus className="h-4 w-4 mr-2" /> New job
        </Button>
      </div>

      {showCreate && <NewJobForm onClose={() => setShowCreate(false)} knownRepos={repoOptions} />}

      <div className="space-y-4">
        {/* On a phone the two filters share the line in halves, so their
            widths never wait on the repo names the jobs poll brings. */}
        <div className="flex items-center gap-2">
          <FilterSelect
            label="Statuses"
            value={statusFilter}
            options={[...ALL_JOB_STATUSES]}
            optionLabel={statusLabel}
            onChange={(v) => setParam("status", v)}
            className="flex-1 sm:flex-initial"
          />
          <FilterSelect
            label="Repos"
            value={repoFilter}
            options={repoOptions}
            onChange={(v) => setParam("repo", v)}
            className="flex-1 sm:flex-initial"
          />
        </div>

        {isError ? (
          <PageError message="Failed to load jobs" onRetry={() => refetch()} />
        ) : boardEmpty ? (
          <EmptyJobsState variant="console" onNewJob={() => setShowCreate(true)} />
        ) : (
          // The first paint draws the lanes anew. Keyed by it, they are new
          // nodes rather than the skeleton lanes moved, and a layout shift
          // counts only nodes that stay and move; to the glide it is a fresh
          // paint, so no lane travels from where its skeleton stood.
          <GlideBoard glide={glide} layout={lanes} paintKey={painted ? paintKey : "first-paint"}>
            <div
              key={painted ? "board" : "first-paint"}
              className={cn(
                "grid grid-cols-1 gap-3 md:grid-cols-3 items-start",
                roomForQuestion ? "xl:grid-cols-[minmax(0,2fr)_repeat(4,minmax(11.5rem,1fr))]" : "xl:grid-cols-5",
              )}
            >
              {BOARD_COLUMNS.map((column) => (
                <BoardColumn
                  key={column.id}
                  column={column}
                  count={lanes[column.id].length}
                  isLoading={isLoading || !painted}
                  paintKey={paintKey}
                >
                  {laneCards(column.id)}
                </BoardColumn>
              ))}
            </div>
          </GlideBoard>
        )}
      </div>
    </div>
  );
}
