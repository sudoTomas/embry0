import { useState } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft, ChevronDown, ChevronUp, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useJob } from "@/hooks/useJobs";
import { useJobLogs } from "@/hooks/useJobLogs";
import { useAgents } from "@/hooks/useAgents";
import { useJobInputs } from "@/hooks/useInputs";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { SummaryMetricsBar } from "@/components/jobs/SummaryMetricsBar";
import { ResultsSummary } from "@/components/jobs/ResultsSummary";
import { ErrorSummary } from "@/components/jobs/ErrorSummary";
import { AwaitingInputCard } from "@/components/jobs/AwaitingInputCard";
import { AgentExecutionCard } from "@/components/agents/AgentExecutionCard";
import { AgentDetailPopup } from "@/components/agents/AgentDetailPopup";
import { StateFlowPanel } from "@/components/state/StateFlowPanel";
import { LogViewer } from "@/components/logs/LogViewer";
import { getPipelinePhases } from "@/lib/pipeline-phases";
import { useJobEvents } from "@/hooks/useJobEvents";
import { JobEventTimeline } from "@/components/jobs/JobEventTimeline";
import type { AgentDefinition, LogEvent, JobInput } from "@/lib/types";

export function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const { data: job, isLoading, isError } = useJob(jobId);
  const { data: agentTypes } = useAgents();

  const isRunning = job?.status === "running";
  const isAwaitingInput = job?.status === "awaiting_input";
  const shouldStream = (isRunning || isAwaitingInput) && !!jobId;

  const logs = useJobLogs(shouldStream ? jobId! : "");
  const { data: jobInputs } = useJobInputs(jobId ?? "");

  const isActive = job?.status === "running" || job?.status === "triaging" || job?.status === "awaiting_input";
  const { events: pipelineEvents, connected: pipelineConnected } = useJobEvents(jobId, isActive || job?.status === "completed" || job?.status === "failed");

  const [selectedAgent, setSelectedAgent] = useState<{ agentType: string; nodeId: string } | null>(null);
  const [logsExpanded, setLogsExpanded] = useState(false);

  if (isError) return <div className="p-8 text-center text-red-400">Failed to load job</div>;
  if (isLoading || !job) return <div className="p-8 text-center text-white/40">Loading...</div>;

  const phases = getPipelinePhases(job.pipeline_graph);

  const agentInfo = selectedAgent
    ? agentTypes?.find((a: AgentDefinition) => a.type === selectedAgent.agentType)
    : undefined;

  const handleAgentClick = (agentType: string, nodeId: string) => {
    setSelectedAgent({ agentType, nodeId });
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link to="/jobs">
          <Button variant="ghost" size="icon">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{job.repo}</h1>
            <JobStatusBadge status={job.status} />
          </div>
          <p className="text-sm text-muted-foreground font-mono">{job.job_id}</p>
        </div>
        <Link to={`/jobs/${job.job_id}/logs`}>
          <Button variant="outline" size="sm" className="gap-1.5">
            View Full Logs <ExternalLink className="w-3 h-3" />
          </Button>
        </Link>
      </div>

      {/* Task description */}
      <div className="px-4 py-3 rounded-lg bg-white/[0.03] border border-white/[0.06] text-sm text-white/70">
        {job.task}
      </div>

      {/* PR link */}
      {job.pr_url && (
        <div className="flex items-center gap-2 text-sm px-4 py-2 rounded-lg bg-white/[0.03] border border-white/[0.06]">
          <span className="text-white/40">PR:</span>
          <a href={job.pr_url} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline">
            {job.pr_url}
          </a>
        </div>
      )}

      {/* Pipeline Events Timeline */}
      {pipelineEvents.length > 0 && (
        <div className="px-4 py-3 rounded-lg bg-white/[0.03] border border-white/[0.06]">
          <h3 className="text-sm font-medium text-white/60 mb-2 flex items-center gap-2">
            Pipeline Events
            {pipelineConnected && <span className="h-2 w-2 rounded-full bg-green-400 animate-pulse" />}
          </h3>
          <JobEventTimeline events={pipelineEvents} connected={pipelineConnected} />
        </div>
      )}

      {/* Status-dependent content */}
      {job.status === "running" && (
        <RunningLayout
          job={job}
          logs={logs}
          phases={phases}
          onAgentClick={handleAgentClick}
        />
      )}

      {job.status === "completed" && (
        <CompletedLayout
          job={job}
          logs={logs}
          phases={phases}
          onAgentClick={handleAgentClick}
        />
      )}

      {job.status === "failed" && (
        <FailedLayout
          job={job}
          logs={logs}
          phases={phases}
          onAgentClick={handleAgentClick}
        />
      )}

      {job.status === "awaiting_input" && (
        <AwaitingLayout
          job={job}
          logs={logs}
          phases={phases}
          jobInputs={jobInputs ?? []}
          onAgentClick={handleAgentClick}
        />
      )}

      {job.status === "pending" && (
        <PendingLayout job={job} phases={phases} />
      )}

      {/* Collapsible log panel (for running/completed/failed) */}
      {["running", "completed", "failed"].includes(job.status) && logs.events.length > 0 && (
        <div>
          <button
            onClick={() => setLogsExpanded(!logsExpanded)}
            className="flex items-center gap-2 text-sm text-white/40 hover:text-white/60 transition-colors mb-2"
          >
            {logsExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            {logsExpanded ? "Hide Logs" : "Show Logs"} ({logs.events.length} events)
          </button>
          {logsExpanded && (
            <LogViewer
              events={logs.events}
              isConnected={logs.isConnected}
              isComplete={logs.isComplete}
              costUsd={logs.latestCost}
              tokensIn={logs.latestTokensIn}
              tokensOut={logs.latestTokensOut}
              turns={logs.latestTurns}
            />
          )}
        </div>
      )}

      {/* Agent detail popup */}
      {selectedAgent && (
        <AgentDetailPopup
          agentType={selectedAgent.agentType}
          agentInfo={agentInfo}
          nodeState={logs.nodeStates[selectedAgent.nodeId]}
          onClose={() => setSelectedAgent(null)}
        />
      )}
    </div>
  );
}

// ===== Status-specific layouts =====

function RunningLayout({
  job,
  logs,
  phases,
  onAgentClick,
}: {
  job: { started_at: string | null; total_cost_usd: number; repo: string; task: string; pr_url: string | null };
  logs: ReturnType<typeof useJobLogs>;
  phases: { agentType: string; agents: string[] }[];
  onAgentClick: (agentType: string, nodeId: string) => void;
}) {
  return (
    <>
      <SummaryMetricsBar
        nodeStates={logs.nodeStates}
        totalPhases={phases.length}
        totalCost={logs.latestCost || job.total_cost_usd}
        startedAt={job.started_at}
        isRunning={true}
      />

      <div className="grid grid-cols-[420px_1fr] gap-5">
        {/* Left panel: Agent execution cards */}
        <div className="space-y-2.5">
          {phases.map((phase) =>
            phase.agents.map((nodeId) => (
              <AgentExecutionCard
                key={nodeId}
                agentType={phase.agentType}
                nodeState={logs.nodeStates[nodeId]}
                liveOutput={getLatestOutput(logs.events, nodeId)}
                onClick={() => onAgentClick(phase.agentType, nodeId)}
              />
            )),
          )}
        </div>

        {/* Right panel: State flow */}
        <StateFlowPanel
          nodeStates={logs.nodeStates}
          phases={phases}
          jobData={{
            repo: job.repo,
            task: job.task,
            totalCost: logs.latestCost || job.total_cost_usd,
            prUrl: job.pr_url,
          }}
          onAgentClick={onAgentClick}
        />
      </div>
    </>
  );
}

function CompletedLayout({
  job,
  logs,
  phases,
  onAgentClick,
}: {
  job: { pr_url: string | null; total_cost_usd: number; started_at: string | null; finished_at: string | null };
  logs: ReturnType<typeof useJobLogs>;
  phases: { agentType: string; agents: string[] }[];
  onAgentClick: (agentType: string, nodeId: string) => void;
}) {
  const completedAgents = Object.values(logs.nodeStates).filter((ns) => ns.state === "completed").length;

  return (
    <>
      <ResultsSummary
        prUrl={job.pr_url}
        totalCost={job.total_cost_usd}
        startedAt={job.started_at}
        finishedAt={job.finished_at}
        agentsRun={completedAgents}
      />

      <StateFlowPanel
        nodeStates={logs.nodeStates}
        phases={phases}
        jobData={{ totalCost: job.total_cost_usd, prUrl: job.pr_url }}
        onAgentClick={onAgentClick}
      />
    </>
  );
}

function FailedLayout({
  job,
  logs,
  phases,
  onAgentClick,
}: {
  job: { error_message: string | null; total_cost_usd: number; started_at: string | null; finished_at: string | null };
  logs: ReturnType<typeof useJobLogs>;
  phases: { agentType: string; agents: string[] }[];
  onAgentClick: (agentType: string, nodeId: string) => void;
}) {
  return (
    <>
      <ErrorSummary
        errorMessage={job.error_message}
        totalCost={job.total_cost_usd}
        startedAt={job.started_at}
        finishedAt={job.finished_at}
        nodeStates={logs.nodeStates}
      />

      <StateFlowPanel
        nodeStates={logs.nodeStates}
        phases={phases}
        jobData={{ totalCost: job.total_cost_usd }}
        onAgentClick={onAgentClick}
      />
    </>
  );
}

function AwaitingLayout({
  job,
  logs,
  phases,
  jobInputs,
  onAgentClick,
}: {
  job: { job_id: string };
  logs: ReturnType<typeof useJobLogs>;
  phases: { agentType: string; agents: string[] }[];
  jobInputs: JobInput[];
  onAgentClick: (agentType: string, nodeId: string) => void;
}) {
  return (
    <>
      <AwaitingInputCard
        pendingInputs={logs.pendingInputs}
        jobInputs={jobInputs}
        jobId={job.job_id}
      />

      <StateFlowPanel
        nodeStates={logs.nodeStates}
        phases={phases}
        onAgentClick={onAgentClick}
      />
    </>
  );
}

function PendingLayout({
  job,
  phases,
}: {
  job: { repo: string; task: string; pipeline_graph?: Record<string, unknown> | null };
  phases: { agentType: string; agents: string[] }[];
}) {
  return (
    <StateFlowPanel
      nodeStates={{}}
      phases={phases}
      jobData={{ repo: job.repo, task: job.task }}
    />
  );
}

// ===== Helpers =====

function getLatestOutput(events: LogEvent[], nodeId: string): string | undefined {
  for (let i = events.length - 1; i >= 0; i--) {
    const evt = events[i];
    if (evt.node_id === nodeId) {
      if (evt.type === "text" && typeof evt.content === "string" && evt.content) {
        return evt.content;
      }
      if (evt.type === "progress" && evt.message) return evt.message;
      if (evt.type === "tool_end" && evt.tool) return `Tool: ${evt.tool}`;
    }
  }
  return undefined;
}
