import { useState, useEffect, useRef } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft, Clock, DollarSign, Layers } from "lucide-react";
import { OperationGlyph } from "@/components/divine/OperationGlyph";
import { DivineRipple } from "@/components/divine/DivineRipple";
import { jobToOperation, shouldFireCompletionFlare } from "@/components/divine/operations";
import { Button, buttonVariants } from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { useJob } from "@/hooks/useJobs";
import { useJobInputs } from "@/hooks/useInputs";
import { useTraces } from "@/hooks/useTraces";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { AwaitingInputCard } from "@/components/jobs/AwaitingInputCard";
import { useJobLogs } from "@/hooks/useJobLogs";
import { useAgentStates } from "@/hooks/useAgentStates";
import { AgentCard } from "@/components/jobs/AgentCard";
import { PausedBanner } from "@/components/jobs/PausedBanner";
import { TracesTable } from "@/components/traces/TracesTable";
import { CostBreakdownTable } from "@/components/jobs/CostBreakdownTable";
import { AgentFailuresCard } from "@/components/jobs/AgentFailuresCard";
import { DeliverablesCard } from "@/components/jobs/DeliverablesCard";
import { QATab } from "@/components/qa/QATab";
import { resumeJob, discardJob } from "@/api/jobs";
import { isTerminalStatus } from "@/lib/jobStatus";
import { isNotFound } from "@/api/errorDetail";
import { PageError } from "@/components/PageError";
import { toastApiError } from "@/api/errorToast";

function useElapsedTime(startedAt: string | null, finishedAt: string | null) {
  const [elapsed, setElapsed] = useState("");
  useEffect(() => {
    if (!startedAt) return;
    const update = () => {
      const start = new Date(startedAt).getTime();
      const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
      const s = Math.floor((end - start) / 1000);
      const m = Math.floor(s / 60);
      const rs = s % 60;
      setElapsed(m > 0 ? `${m}m ${rs}s` : `${rs}s`);
    };
    update();
    if (!finishedAt) {
      const interval = setInterval(update, 1000);
      return () => clearInterval(interval);
    }
  }, [startedAt, finishedAt]);
  return elapsed;
}

const TRACES_PAGE_SIZE = 25;

export function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const { data: job, isLoading, isError, error, refetch } = useJob(jobId);
  const missing = isNotFound(error);
  const { data: jobInputs } = useJobInputs(missing ? "" : (jobId ?? ""), job?.status);

  // A job that does not exist has no event stream to open.
  const { events, isConnected, latestCost, pipelineGraph } = useJobLogs(missing ? undefined : jobId);
  const { agents, prUrl, interruptData } = useAgentStates(events, job?.status, pipelineGraph);
  const activeAgents = agents.filter((a) => a.status === "running");
  const completedAgents = agents.filter((a) => a.status === "completed" || a.status === "failed");
  const pendingAgents = agents.filter((a) => a.status === "pending");

  // Traces tab state
  const [traceFilters, setTraceFilters] = useState<{ agent_type?: string; result?: string }>({});
  const [traceOffset, setTraceOffset] = useState(0);
  const { data: tracesData } = useTraces(
    jobId
      ? { job_id: jobId, ...traceFilters, limit: TRACES_PAGE_SIZE, offset: traceOffset }
      : {},
  );

  const elapsed = useElapsedTime(job?.started_at ?? null, job?.finished_at ?? null);

  // Cost sources disagree by design: the stream-derived sums only cover agents
  // whose events this viewer actually received, so a viewer who joined mid-job
  // sees a fraction of the spend. job.total_cost_usd is the persisted truth and
  // trails only the agent currently running — take whichever is highest.
  const liveCost = agents.reduce((sum, a) => sum + a.costUsd, 0);
  const displayCost = Math.max(liveCost, latestCost, job?.total_cost_usd ?? 0);

  // Phase counter
  const totalPhases = agents.length;
  const completedPhases = completedAgents.length;

  const currentOperation = jobToOperation(agents);

  // Completion flare: increment flareKey when job.status transitions into a
  // terminal-success state (completed | pr_merged). The DivineRipple is keyed
  // on it so each transition mounts a fresh one-shot. First mount of an
  // already-complete job is suppressed by shouldFireCompletionFlare.
  const [flareKey, setFlareKey] = useState(0);
  const prevStatusRef = useRef<string | null>(null);
  useEffect(() => {
    if (shouldFireCompletionFlare(prevStatusRef.current, job?.status)) {
      setFlareKey((k) => k + 1);
    }
    prevStatusRef.current = job?.status ?? null;
  }, [job?.status]);

  if (missing) return <PageError message={`No job ${jobId}`} back={{ to: "/jobs", label: "All jobs" }} />;
  if (isError)
    return <PageError message="Failed to load job" onRetry={() => void refetch()} back={{ to: "/jobs", label: "All jobs" }} />;
  if (isLoading || !job) return <div className="p-8 text-center text-white/40">Loading...</div>;

  const displayPrUrl = prUrl || job.pr_url;
  const isActive = job.status === "running" || job.status === "awaiting_input";

  const handleResume = async (choice: string, guidance?: string) => {
    try {
      await resumeJob(job.job_id, choice, guidance);
      refetch();
    } catch (err) {
      toastApiError(err, "Failed to resume job");
    }
  };

  const handleDiscard = async () => {
    try {
      await discardJob(job.job_id);
      refetch();
    } catch (err) {
      toastApiError(err, "Failed to discard job");
    }
  };

  return (
    <div className="space-y-4">
      {/* Compact Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <Link to="/jobs">
          <Button variant="ghost" size="icon" className="h-8 w-8">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <span className="relative flex items-center justify-center w-8 h-8 shrink-0">
          <OperationGlyph operation={currentOperation} size={28} titled />
          {flareKey > 0 && (
            <span className="absolute inset-0 flex items-center justify-center pointer-events-none">
              <DivineRipple key={flareKey} size={64} />
            </span>
          )}
        </span>
        <div className="min-w-[12rem] flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-section font-bold">{job.repo}</span>
            <JobStatusBadge status={job.status} />
          </div>
          <div className="text-[10px] text-white/30 font-mono">{job.job_id}</div>
        </div>
        <div className="flex items-center gap-4 text-xs text-white/50">
          {elapsed && (
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" /> {elapsed}
            </span>
          )}
          {displayCost > 0 && (
            <span className="flex items-center gap-1">
              <DollarSign className="w-3 h-3" /> ${displayCost.toFixed(2)}
            </span>
          )}
          {totalPhases > 0 && (
            <span className="flex items-center gap-1">
              <Layers className="w-3 h-3" /> {completedPhases}/{totalPhases}
            </span>
          )}
        </div>
        {/* The job's issue row, which links on to its tracker (GitHub, GitLab
            or Linear): a link built here assumed github.com for every forge. */}
        {job.issue_id && (
          <Link
            to={`/issues/${job.issue_id}`}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }), "gap-1 text-xs h-7")}
          >
            {job.issue_number ? `Issue #${job.issue_number}` : "Issue"}
          </Link>
        )}
      </div>

      {/* Task description */}
      <div className="px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.05] text-xs text-white/60 line-clamp-3">
        {job.task}
      </div>

      {/* Progress bar */}
      {isActive && (
        <div className="h-[3px] bg-white/[0.06] rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${totalPhases > 0 ? ((completedPhases + activeAgents.length * 0.5) / totalPhases) * 100 : 0}%`,
              background: "var(--color-primary)",
            }}
          />
        </div>
      )}

      {/* PR link */}
      {displayPrUrl && (
        <div className="flex items-center gap-2 text-xs px-3 py-2 rounded-md bg-purple/[0.08] border border-purple/20">
          <span className="text-purple">&#x1F517;</span>
          <a href={displayPrUrl} target="_blank" rel="noopener noreferrer" className="text-purple hover:underline truncate">
            {displayPrUrl}
          </a>
        </div>
      )}

      {/* Success banner: the job completed, or its pull request has merged since */}
      {(job.status === "completed" || job.status === "pr_merged") && (
        <div className="px-4 py-3 rounded-lg border border-success/20 bg-success/[0.06] flex items-center gap-3">
          <span className="text-success text-lg">&#x2713;</span>
          <div>
            <div className="text-sm font-medium text-success">
              {job.status === "pr_merged" ? "Pipeline completed, pull request merged" : "Pipeline completed successfully"}
            </div>
            {displayPrUrl && (
              <a href={displayPrUrl} target="_blank" rel="noopener noreferrer" className="text-xs text-purple hover:underline">
                {displayPrUrl}
              </a>
            )}
          </div>
        </div>
      )}

      {/* Deliverables (RAV-603): what the job actually produced — PR link,
          report text, downloadable artifacts — for every finished status (a
          completed job becomes pr_merged; a partial or skipped one keeps its
          report). The card renders nothing while the list is empty. */}
      {isTerminalStatus(job.status) && <DeliverablesCard jobId={job.job_id} />}

      {/* Partial: some apps passed, some did not */}
      {job.status === "partial" && (
        <div className="px-4 py-3 rounded-lg border border-warning/20 bg-warning/[0.06]">
          <div className="text-sm font-medium text-warning mb-1">Pipeline partly passed</div>
          {job.error_message && <div className="text-xs text-white/50">{job.error_message}</div>}
        </div>
      )}

      {/* Error banner */}
      {job.status === "failed" && job.error_message && (
        <div className="px-4 py-3 rounded-lg border border-destructive/20 bg-destructive/[0.06]">
          <div className="text-sm font-medium text-destructive mb-1">&#x2717; Pipeline failed</div>
          <div className="text-xs text-white/50">{job.error_message}</div>
          {job.error_code && (
            <div className="mt-1 text-[11px] font-mono text-destructive/70">{job.error_code}</div>
          )}
        </div>
      )}

      <AgentFailuresCard failures={job.agent_failures} />

      {/* Expired banner */}
      {job.status === "expired" && (
        <div className="px-4 py-3 rounded-lg border border-border bg-overlay">
          <div className="text-sm text-fg-muted">Job expired — sandbox discarded</div>
        </div>
      )}

      {/* Paused banner */}
      {job.status === "paused" && (
        <PausedBanner
          jobId={job.job_id}
          reason={interruptData?.reason || job.current_stage || "paused"}
          retryCount={interruptData?.retry_count}
          latestReview={interruptData?.latest_review}
          prUrl={displayPrUrl || undefined}
          pausedAt={job.finished_at || job.started_at || undefined}
          onResume={handleResume}
          onDiscard={handleDiscard}
        />
      )}

      {/* Awaiting input */}
      {job.status === "awaiting_input" && (
        <AwaitingInputCard
          jobInputs={jobInputs ?? []}
          jobId={job.job_id}
        />
      )}

      {/* Pipeline / Traces Tabs */}
      <Tabs defaultValue={job.pipeline_template === "qa" ? "qa" : "pipeline"} className="space-y-3">
        <TabsList>
          <TabsTrigger value="pipeline">Pipeline</TabsTrigger>
          <TabsTrigger value="traces">
            Traces{tracesData ? ` (${tracesData.total})` : ""}
          </TabsTrigger>
          {job.pipeline_template === "qa" && (
            <TabsTrigger value="qa">QA</TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="pipeline">
          {/* Agent Cards Grid */}
          <div className="space-y-2">
            {/* Completed agents in grid */}
            {completedAgents.length > 0 && (
              <div className={`grid gap-2 ${completedAgents.length >= 3 ? "grid-cols-1 sm:grid-cols-3" : completedAgents.length === 2 ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1"}`}>
                {completedAgents.map((agent) => (
                  <AgentCard key={agent.runId} agent={agent} />
                ))}
              </div>
            )}

            {/* Active agents — full width or side by side */}
            {activeAgents.length > 0 && (
              <div className={`grid gap-2 ${activeAgents.length >= 2 ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1"}`}>
                {activeAgents.map((agent) => (
                  <AgentCard key={agent.runId} agent={agent} expanded />
                ))}
              </div>
            )}

            {/* Pending agents in grid */}
            {pendingAgents.length > 0 && (
              <div className={`grid gap-2 ${pendingAgents.length >= 3 ? "grid-cols-1 sm:grid-cols-3" : pendingAgents.length === 2 ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1"}`}>
                {pendingAgents.map((agent) => (
                  <AgentCard key={agent.runId} agent={agent} />
                ))}
              </div>
            )}
          </div>
        </TabsContent>

        <TabsContent value="traces">
          {job.cost_breakdown && job.cost_breakdown.length > 0 && (
            <div className="mb-4">
              <CostBreakdownTable breakdown={job.cost_breakdown} />
            </div>
          )}
          <TracesTable
            traces={tracesData?.traces ?? []}
            total={tracesData?.total ?? 0}
            offset={traceOffset}
            limit={TRACES_PAGE_SIZE}
            filters={traceFilters}
            onFilterChange={(f) => {
              setTraceFilters(f);
              setTraceOffset(0);
            }}
            onPageChange={setTraceOffset}
          />
        </TabsContent>

        {job.pipeline_template === "qa" && (
          <TabsContent value="qa">
            <QATab jobId={job.job_id} jobIsLive={job.status === "running"} />
          </TabsContent>
        )}
      </Tabs>

      {/* Connection indicator */}
      {job.status !== "pending" && job.status !== "expired" && (
        <div className="flex items-center gap-2 text-[10px] text-white/30">
          <span className={`h-1.5 w-1.5 rounded-full ${isConnected ? "bg-success animate-pulse" : "bg-white/20"}`} />
          <span>{isConnected ? "Live" : "Disconnected"}</span>
          <span className="ml-auto">{events.length} events</span>
        </div>
      )}

      {/* Pending state */}
      {job.status === "pending" && (
        <div className="text-center py-12 text-white/30 text-sm">
          Waiting to start...
        </div>
      )}
    </div>
  );
}
