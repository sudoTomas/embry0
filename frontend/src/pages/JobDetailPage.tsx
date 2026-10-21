import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { OperationGlyph } from "@/components/divine/OperationGlyph";
import { DivineRipple } from "@/components/divine/DivineRipple";
import { jobToOperation, shouldFireCompletionFlare } from "@/components/divine/operations";
import { Button } from "@/components/ui/Button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { useJob } from "@/hooks/useJobs";
import { useJobInputs } from "@/hooks/useInputs";
import { useTraces } from "@/hooks/useTraces";
import { useJobLogs } from "@/hooks/useJobLogs";
import { useAgentStates } from "@/hooks/useAgentStates";
import { useElapsedTime } from "@/hooks/useElapsedTime";
import { useQaAttempts } from "@/hooks/useQaResults";
import { useJobVerification } from "@/hooks/useJobVerification";
import { useAgentIdentity } from "@/hooks/useAgentIdentity";
import { deriveNodeStates, filterEventsForNode, physicalOf } from "@/lib/pipeline-live";
import { isTerminalStatus } from "@/lib/jobStatus";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { AwaitingInputCard } from "@/components/jobs/AwaitingInputCard";
import { AutoAnswerCard } from "@/components/jobs/AutoAnswerCard";
import { AgentCard } from "@/components/jobs/AgentCard";
import { PausedBanner } from "@/components/jobs/PausedBanner";
import { PartyStrip } from "@/components/jobs/PartyStrip";
import { JobHud } from "@/components/jobs/JobHud";
import { VerificationTab } from "@/components/jobs/VerificationTab";
import { TracesTable } from "@/components/traces/TracesTable";
import { CostBreakdownTable } from "@/components/jobs/CostBreakdownTable";
import { AgentFailuresCard } from "@/components/jobs/AgentFailuresCard";
import { DeliverablesCard } from "@/components/jobs/DeliverablesCard";
import { PipelineGraphView } from "@/components/pipeline/PipelineGraphView";
import { ConversationView } from "@/components/logs/ConversationView";
import { StructuredView } from "@/components/logs/StructuredView";
import { RawView } from "@/components/logs/RawView";
import { TranscriptDrawer } from "@/components/logs/TranscriptDrawer";
import { QATab } from "@/components/qa/QATab";
import { resumeJob, discardJob } from "@/api/jobs";

const TRACES_PAGE_SIZE = 25;

export function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const { data: job, isLoading, isError, refetch } = useJob(jobId);
  const { data: jobInputs, refetch: refetchInputs } = useJobInputs(jobId ?? "");

  const { events, isConnected, isComplete, latestCost, latestTokensIn, latestTokensOut, latestTurns, pipelineGraph } = useJobLogs(jobId);
  const { agents, prUrl, interruptData, pausedForInput } = useAgentStates(events, job?.status, pipelineGraph);
  const jobFinished = isTerminalStatus(job?.status);
  const live = !!job && !jobFinished && !isComplete;

  const nodeStates = useMemo(() => deriveNodeStates(pipelineGraph, events, job?.status), [pipelineGraph, events, job?.status]);

  // The run is the unit of selection: the strip, the map and the Conversation tab share it.
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const selectedRun = agents.find((a) => a.runId === selectedRunId) ?? null;
  const selectedNodeId = useMemo(() => {
    if (!selectedRun || !pipelineGraph) return null;
    return pipelineGraph.nodes.find((n) => physicalOf(n) === selectedRun.node)?.node_id ?? null;
  }, [selectedRun, pipelineGraph]);
  const handleNodeSelect = useCallback(
    (nodeId: string | null) => {
      if (!nodeId || !pipelineGraph) { setSelectedRunId(null); return; }
      const node = pipelineGraph.nodes.find((n) => n.node_id === nodeId);
      const physical = node ? physicalOf(node) : nodeId;
      const run = [...agents].reverse().find((a) => a.node === physical);
      setSelectedRunId(run?.runId ?? null);
    },
    [agents, pipelineGraph],
  );
  const recordEvents = useMemo(() => {
    if (!selectedRun) return events;
    if (selectedRun.runId.startsWith("pending:") || selectedRun.runId.startsWith("node:")) {
      return filterEventsForNode(pipelineGraph, events, selectedNodeId);
    }
    return selectedRun.events;
  }, [selectedRun, selectedNodeId, events, pipelineGraph]);

  // Transcript drawer (spec 3.6)
  const [drawerRun, setDrawerRun] = useState<{ runId: string; agent: string } | null>(null);
  const drawerIdentity = useAgentIdentity(drawerRun?.agent);

  // Banners fed by the stream, not only the poll: a pause or interrupt the WS
  // carried refreshes the job row and its inputs at once. Keyed on booleans:
  // interruptData is rebuilt with every event batch, and keying on the object
  // would refetch on each batch after the interrupt, not on the interrupt.
  const interrupted = interruptData !== null;
  useEffect(() => {
    if (pausedForInput || interrupted) { refetch(); refetchInputs(); }
  }, [pausedForInput, interrupted, refetch, refetchInputs]);

  const [traceFilters, setTraceFilters] = useState<{ agent_type?: string; result?: string }>({});
  const [traceOffset, setTraceOffset] = useState(0);
  const { data: tracesData } = useTraces(jobId ? { job_id: jobId, ...traceFilters, limit: TRACES_PAGE_SIZE, offset: traceOffset } : {});

  const { data: qaAttempts } = useQaAttempts(jobId ?? "", live);
  const hasQa = events.some((e) => e.type === "qa_apps_resolved") || (qaAttempts?.length ?? 0) > 0;
  const { data: verification } = useJobVerification(jobId, live);
  const hasVerification = !!verification || events.some((e) => e.type === "verification_result");

  const elapsed = useElapsedTime(job?.started_at ?? null, job?.finished_at ?? null);
  // Cost sources disagree by design: the stream-derived sums only cover agents
  // whose events this viewer received; job.total_cost_usd is the persisted
  // truth and trails only the running agent. Take whichever is highest.
  const liveCost = agents.reduce((sum, a) => sum + a.costUsd, 0);
  const displayCost = Math.max(liveCost, latestCost, job?.total_cost_usd ?? 0);
  const budgetUsd = (job?.pipeline_config?.max_budget_usd as number | undefined) ?? pipelineGraph?.metadata.max_total_budget_usd ?? null;

  const currentOperation = jobToOperation(agents);
  const [flareKey, setFlareKey] = useState(0);
  const prevStatusRef = useRef<string | null>(null);
  useEffect(() => {
    if (shouldFireCompletionFlare(prevStatusRef.current, job?.status)) setFlareKey((k) => k + 1);
    prevStatusRef.current = job?.status ?? null;
  }, [job?.status]);

  if (isError) return <div className="p-8 text-center text-destructive">Failed to load job</div>;
  if (isLoading || !job) return <div className="p-8 text-center text-muted-foreground">Loading...</div>;

  const displayPrUrl = prUrl || job.pr_url;
  const handleResume = async (choice: string, guidance?: string) => {
    try { await resumeJob(job.job_id, choice, guidance); refetch(); }
    catch (err) { toast.error(err instanceof Error ? err.message : "Failed to resume job"); }
  };
  const handleDiscard = async () => {
    try { await discardJob(job.job_id); refetch(); }
    catch (err) { toast.error(err instanceof Error ? err.message : "Failed to discard job"); }
  };
  const autoAnswered = (jobInputs ?? []).filter((inp) => inp.status === "auto_answered" || (inp.status === "answered" && inp.auto_answer != null));

  return (
    <div className="space-y-4">
      {/* 1. Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <Link to="/jobs"><Button variant="ghost" size="icon" className="h-8 w-8"><ArrowLeft className="h-4 w-4" /></Button></Link>
        <span className="relative flex items-center justify-center w-8 h-8 shrink-0">
          <OperationGlyph operation={currentOperation} size={28} titled />
          {flareKey > 0 && <span className="absolute inset-0 flex items-center justify-center pointer-events-none"><DivineRipple key={flareKey} size={64} /></span>}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="title-pixel text-base truncate">{job.repo}</span>
            <JobStatusBadge status={job.status} />
          </div>
          <div className="text-[10px] text-muted-foreground font-mono">{job.job_id}</div>
        </div>
        {job.issue_number && (
          <a href={`https://github.com/${job.repo}/issues/${job.issue_number}`} target="_blank" rel="noopener noreferrer">
            <Button variant="outline" size="sm" className="gap-1 text-xs h-7">Issue #{job.issue_number} <ExternalLink className="w-3 h-3" /></Button>
          </a>
        )}
        {displayPrUrl && (
          <a href={displayPrUrl} target="_blank" rel="noopener noreferrer">
            <Button variant="outline" size="sm" className="gap-1 text-xs h-7">Pull request <ExternalLink className="w-3 h-3" /></Button>
          </a>
        )}
      </div>

      <div className="px-3 py-2 rounded-md bg-card border border-border text-xs text-muted-foreground line-clamp-3">{job.task}</div>

      {/* 2. Party strip */}
      <PartyStrip agents={agents} selectedRunId={selectedRunId} onSelect={setSelectedRunId} />

      {/* 3. HUD */}
      <JobHud status={job.status} elapsed={elapsed} costUsd={displayCost} turns={latestTurns} tokensIn={latestTokensIn} tokensOut={latestTokensOut} budgetUsd={budgetUsd} />

      {/* 4. Banners */}
      {job.status === "completed" && (
        <div className="px-4 py-3 rounded-lg border border-palette-emerald/30 bg-palette-emerald/10 text-sm text-palette-emerald">Pipeline completed successfully</div>
      )}
      {job.status === "completed" && <DeliverablesCard jobId={job.job_id} />}
      {job.status === "failed" && job.error_message && (
        <div className="px-4 py-3 rounded-lg border border-destructive/30 bg-destructive/10">
          <div className="text-sm font-medium text-destructive mb-1">Pipeline failed</div>
          <div className="text-xs text-muted-foreground">{job.error_message}</div>
          {job.error_code && <div className="mt-1 text-[11px] font-mono text-destructive/80">{job.error_code}</div>}
        </div>
      )}
      <AgentFailuresCard failures={job.agent_failures} />
      {job.status === "expired" && <div className="px-4 py-3 rounded-lg border border-border bg-card text-sm text-muted-foreground">Job expired — sandbox discarded</div>}
      {job.status === "paused" && (
        <PausedBanner jobId={job.job_id} reason={interruptData?.reason || job.current_stage || "paused"} retryCount={interruptData?.retry_count} latestReview={interruptData?.latest_review} prUrl={displayPrUrl || undefined} pausedAt={job.finished_at || job.started_at || undefined} onResume={handleResume} onDiscard={handleDiscard} />
      )}
      {job.status === "awaiting_input" && <AwaitingInputCard jobInputs={jobInputs ?? []} jobId={job.job_id} />}
      {autoAnswered.length > 0 && <div className="space-y-2">{autoAnswered.map((inp) => <AutoAnswerCard key={inp.input_id} input={inp} jobId={job.job_id} />)}</div>}
      {job.status === "pending" && <div className="text-center py-12 text-muted-foreground text-sm">Waiting to start...</div>}

      {/* 5. Party map and the record */}
      <div className={`grid gap-4 ${pipelineGraph ? "lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]" : ""}`}>
        {pipelineGraph && (
          <div className="rounded-lg border border-border bg-card p-2 min-h-[320px]">
            <PipelineGraphView graph={pipelineGraph} nodeStates={nodeStates} onNodeSelect={handleNodeSelect} selectedNodeId={selectedNodeId} />
          </div>
        )}
        <div className="flex flex-col min-h-0 lg:max-h-[calc(100vh-14rem)]">
          <Tabs defaultValue="conversation" className="flex flex-col min-h-0 flex-1 space-y-3">
            <TabsList>
              <TabsTrigger value="conversation">Conversation</TabsTrigger>
              <TabsTrigger value="structured">Structured</TabsTrigger>
              <TabsTrigger value="raw">Raw</TabsTrigger>
              {hasQa && <TabsTrigger value="qa">QA</TabsTrigger>}
              {hasVerification && <TabsTrigger value="verification">Verification</TabsTrigger>}
            </TabsList>
            <TabsContent value="conversation" className="flex flex-col min-h-0 flex-1 gap-3">
              {selectedRun && (
                <div data-testid="selected-run" data-run-id={selectedRun.runId}>
                  <AgentCard agent={selectedRun} expanded />
                  <button type="button" onClick={() => setSelectedRunId(null)} className="mt-1 text-xs underline text-muted-foreground hover:text-foreground">Show every run</button>
                </div>
              )}
              <ConversationView events={recordEvents} onFullTranscript={setDrawerRun} />
            </TabsContent>
            <TabsContent value="structured" className="flex flex-col min-h-0 flex-1"><StructuredView events={recordEvents} isComplete={isComplete || jobFinished} /></TabsContent>
            <TabsContent value="raw" className="flex flex-col min-h-0 flex-1"><RawView events={recordEvents} /></TabsContent>
            {hasQa && <TabsContent value="qa"><QATab jobId={job.job_id} jobIsLive={job.status === "running"} /></TabsContent>}
            {hasVerification && (
              <TabsContent value="verification">
                {verification ? <VerificationTab verification={verification} /> : <p className="text-sm text-muted-foreground">The verification result is being recorded…</p>}
              </TabsContent>
            )}
          </Tabs>
        </div>
      </div>

      {/* 6. Ledger */}
      <details data-testid="ledger" className="rounded-lg border border-border bg-card">
        <summary className="cursor-pointer px-3 py-2 text-sm hud">Ledger{tracesData ? ` · ${tracesData.total} traces` : ""}</summary>
        <div className="p-3 space-y-4">
          {job.cost_breakdown && job.cost_breakdown.length > 0 && <CostBreakdownTable breakdown={job.cost_breakdown} />}
          <TracesTable traces={tracesData?.traces ?? []} total={tracesData?.total ?? 0} offset={traceOffset} limit={TRACES_PAGE_SIZE} filters={traceFilters} onFilterChange={(f) => { setTraceFilters(f); setTraceOffset(0); }} onPageChange={setTraceOffset} />
        </div>
      </details>

      {/* 7. Footer */}
      {job.status !== "pending" && job.status !== "expired" && (
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground">
          <span className={`h-1.5 w-1.5 rounded-full ${isConnected ? "bg-palette-emerald" : "bg-muted"}`} />
          <span>{isConnected ? "Live" : jobFinished ? "Finished" : "Disconnected"}</span>
          <span className="ml-auto">{events.length} events</span>
        </div>
      )}

      <TranscriptDrawer open={drawerRun !== null} onClose={() => setDrawerRun(null)} jobId={job.job_id} agentType={drawerRun?.agent ?? null} displayName={drawerIdentity.displayName} />
    </div>
  );
}
