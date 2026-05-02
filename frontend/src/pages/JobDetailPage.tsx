import { useState, useEffect } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft, ExternalLink, Clock, DollarSign, Layers } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/Button";
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
import { QATab } from "@/components/qa/QATab";
import { resumeJob, discardJob } from "@/api/jobs";

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
  const { data: job, isLoading, isError, refetch } = useJob(jobId);
  const { data: jobInputs } = useJobInputs(jobId ?? "");

  const { events, isConnected, latestCost } = useJobLogs(jobId);
  const { agents, activeAgents, completedAgents, pendingAgents, prUrl, interruptData } = useAgentStates(
    events,
    job?.status,
  );

  // Traces tab state
  const [traceFilters, setTraceFilters] = useState<{ agent_type?: string; result?: string }>({});
  const [traceOffset, setTraceOffset] = useState(0);
  const { data: tracesData } = useTraces(
    jobId
      ? { job_id: jobId, ...traceFilters, limit: TRACES_PAGE_SIZE, offset: traceOffset }
      : {},
  );

  const elapsed = useElapsedTime(job?.started_at ?? null, job?.finished_at ?? null);

  // Calculate live cost from agent states, falling back to hook cost or job cost
  const liveCost = agents.reduce((sum, a) => sum + a.costUsd, 0);
  const displayCost = liveCost > 0 ? liveCost : latestCost > 0 ? latestCost : job?.total_cost_usd ?? 0;

  // Phase counter
  const totalPhases = agents.length + pendingAgents.length;
  const completedPhases = completedAgents.length;

  if (isError) return <div className="p-8 text-center text-red-400">Failed to load job</div>;
  if (isLoading || !job) return <div className="p-8 text-center text-white/40">Loading...</div>;

  const displayPrUrl = prUrl || job.pr_url;
  const isActive = job.status === "running" || job.status === "awaiting_input";

  const handleResume = async (choice: string, guidance?: string) => {
    try {
      await resumeJob(job.job_id, choice, guidance);
      refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to resume job");
    }
  };

  const handleDiscard = async () => {
    try {
      await discardJob(job.job_id);
      refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to discard job");
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
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-bold text-sm truncate">{job.repo}</span>
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
        {job.issue_number && (
          <a href={`https://github.com/${job.repo}/issues/${job.issue_number}`} target="_blank" rel="noopener noreferrer">
            <Button variant="outline" size="sm" className="gap-1 text-xs h-7">
              Issue #{job.issue_number} <ExternalLink className="w-3 h-3" />
            </Button>
          </a>
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
              background: "linear-gradient(90deg, #06b6d4, #a855f7)",
            }}
          />
        </div>
      )}

      {/* PR link */}
      {displayPrUrl && (
        <div className="flex items-center gap-2 text-xs px-3 py-2 rounded-md bg-violet-500/[0.08] border border-violet-500/20">
          <span className="text-violet-300">&#x1F517;</span>
          <a href={displayPrUrl} target="_blank" rel="noopener noreferrer" className="text-violet-400 hover:underline truncate">
            {displayPrUrl}
          </a>
        </div>
      )}

      {/* Success banner */}
      {job.status === "completed" && (
        <div className="px-4 py-3 rounded-lg border border-green-500/20 bg-green-500/[0.06] flex items-center gap-3">
          <span className="text-green-400 text-lg">&#x2713;</span>
          <div>
            <div className="text-sm font-medium text-green-300">Pipeline completed successfully</div>
            {displayPrUrl && (
              <a href={displayPrUrl} target="_blank" rel="noopener noreferrer" className="text-xs text-violet-400 hover:underline">
                {displayPrUrl}
              </a>
            )}
          </div>
        </div>
      )}

      {/* Error banner */}
      {job.status === "failed" && job.error_message && (
        <div className="px-4 py-3 rounded-lg border border-red-500/20 bg-red-500/[0.06]">
          <div className="text-sm font-medium text-red-400 mb-1">&#x2717; Pipeline failed</div>
          <div className="text-xs text-white/50">{job.error_message}</div>
        </div>
      )}

      {/* Expired banner */}
      {job.status === "expired" && (
        <div className="px-4 py-3 rounded-lg border border-zinc-500/20 bg-zinc-500/[0.06]">
          <div className="text-sm text-zinc-400">Job expired — sandbox discarded</div>
        </div>
      )}

      {/* Paused banner */}
      {job.status === "paused" && (
        <PausedBanner
          jobId={job.job_id}
          reason={interruptData?.reason || "max_retries"}
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
          pendingInputs={{}}
          jobInputs={jobInputs ?? []}
          jobId={job.job_id}
        />
      )}

      {/* Pipeline / Traces Tabs */}
      <Tabs defaultValue="pipeline" className="space-y-3">
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
              <div className={`grid gap-2 ${completedAgents.length >= 3 ? "grid-cols-3" : completedAgents.length === 2 ? "grid-cols-2" : "grid-cols-1"}`}>
                {completedAgents.map((agent) => (
                  <AgentCard key={`${agent.node}-${agent.retryLabel || ""}`} agent={agent} />
                ))}
              </div>
            )}

            {/* Active agents — full width or side by side */}
            {activeAgents.length > 0 && (
              <div className={`grid gap-2 ${activeAgents.length >= 2 ? "grid-cols-2" : "grid-cols-1"}`}>
                {activeAgents.map((agent) => (
                  <AgentCard key={`${agent.node}-active`} agent={agent} expanded />
                ))}
              </div>
            )}

            {/* Pending agents in grid */}
            {pendingAgents.length > 0 && (
              <div className={`grid gap-2 ${pendingAgents.length >= 3 ? "grid-cols-3" : pendingAgents.length === 2 ? "grid-cols-2" : "grid-cols-1"}`}>
                {pendingAgents.map((agent) => (
                  <AgentCard key={`${agent.node}-pending`} agent={agent} />
                ))}
              </div>
            )}
          </div>
        </TabsContent>

        <TabsContent value="traces">
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
          <span className={`h-1.5 w-1.5 rounded-full ${isConnected ? "bg-green-400 animate-pulse" : "bg-white/20"}`} />
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
