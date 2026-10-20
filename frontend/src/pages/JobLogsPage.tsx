import { useMemo, useState, useCallback } from "react";
import { useParams, Link } from "react-router";
import { useJobLogs } from "@/hooks/useJobLogs";
import { useJob } from "@/hooks/useJobs";
import { useJobInputs } from "@/hooks/useInputs";
import { deriveNodeStates, filterEventsForNode } from "@/lib/pipeline-live";
import { isTerminalStatus } from "@/lib/jobStatus";
import { isNotFound } from "@/api/errorDetail";
import { PageError } from "@/components/PageError";
import { LogViewer } from "@/components/logs/LogViewer";
import { PipelineGraphView } from "@/components/pipeline/PipelineGraphView";
import { AgentFilterTabs } from "@/components/logs/AgentFilterTabs";
import { AutoAnswerCard } from "@/components/jobs/AutoAnswerCard";
import { Button, buttonVariants } from "@/components/ui/Button";
import { ArrowLeft, X } from "lucide-react";
import { cn } from "@/lib/utils";

export function JobLogsPage() {
  const { jobId } = useParams<{ jobId: string }>();
  // Load job data for completion detection. RAV-1285: the graph arrives on
  // the `pipeline_graph` event plan_route emits (persisted + WS-replayed,
  // so late joiners get it too); per-node live state is derived from the
  // node_started/node_completed/qa_* events the stream already carries.
  const { data: job, error: jobError } = useJob(jobId);
  const missing = isNotFound(jobError);
  // The event stream opens once the job is known to exist: a missing job
  // has none, and opening it before the 404 mints a token for nothing.
  const {
    events,
    isConnected,
    isComplete,
    latestCost,
    latestTokensIn,
    latestTokensOut,
    latestTurns,
    pipelineGraph,
  } = useJobLogs(job ? jobId : undefined);

  const effectiveGraph = pipelineGraph ?? null;
  const jobFinished = isTerminalStatus(job?.status);
  const effectiveIsComplete = isComplete || jobFinished;
  const effectiveCost = latestCost || job?.total_cost_usd || 0;

  const nodeStates = useMemo(
    () => deriveNodeStates(effectiveGraph, events, job?.status),
    [effectiveGraph, events, job?.status],
  );

  const { data: jobInputs } = useJobInputs(missing ? "" : (jobId ?? ""), job?.status);

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const handleNodeSelect = useCallback((nodeId: string | null) => {
    setSelectedNodeId(nodeId);
  }, []);

  // The selected id is a graph node id; events carry the LangGraph node.
  const filteredEvents = useMemo(
    () => filterEventsForNode(effectiveGraph, events, selectedNodeId),
    [effectiveGraph, events, selectedNodeId],
  );

  // Find the label for the selected node
  const selectedNodeLabel = useMemo(() => {
    if (!selectedNodeId || !effectiveGraph) return null;
    const node = effectiveGraph.nodes.find((n) => n.node_id === selectedNodeId);
    return node?.label || node?.agent_type || selectedNodeId;
  }, [selectedNodeId, effectiveGraph]);

  if (missing) return <PageError message={`No job ${jobId}`} back={{ to: "/jobs", label: "All jobs" }} />;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <Link
          to="/jobs"
          aria-label="Back to jobs"
          className={cn(buttonVariants({ variant: "ghost", size: "icon" }))}
        >
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div>
          <h1 className="text-xl font-bold">Agent Logs</h1>
          <p className="text-sm text-muted-foreground font-mono">{jobId}</p>
        </div>
        <Link to={`/jobs/${jobId}`}>
          <Button variant="outline" size="sm" className="gap-1.5">
            <ArrowLeft className="w-3 h-3" />
            Dashboard
          </Button>
        </Link>
      </div>

      {/* Pipeline Visualization. The note that stands in for the graph is as
          tall as the graph (PipelineGraphView's h-[40vh]): the graph arrives
          on the event stream a second or so after the page paints, and a
          short note there pushed the whole transcript down when it came. */}
      <div className="rounded-lg border border-border bg-card p-2">
        {effectiveGraph ? (
          <PipelineGraphView
            graph={effectiveGraph}
            nodeStates={nodeStates}
            onNodeSelect={handleNodeSelect}
          />
        ) : (
          <div
            data-testid="pipeline-graph-pending"
            className="flex h-[40vh] items-center justify-center px-4 text-center text-sm text-white/20"
          >
            <p>
              The pipeline graph appears once the job's first node emits it.
              Jobs that predate the live graph have none — use the{" "}
              <Link to={`/jobs/${jobId}`} className="underline hover:text-white/40">
                job detail page
              </Link>{" "}
              stage timeline instead.
            </p>
          </div>
        )}
      </div>

      {/* Auto-answered inputs */}
      {jobInputs && jobInputs.filter((inp) => inp.status === "auto_answered" || (inp.status === "answered" && inp.auto_answer != null)).length > 0 && (
        <div className="space-y-2">
          {jobInputs
            .filter((inp) => inp.status === "auto_answered" || (inp.status === "answered" && inp.auto_answer != null))
            .map((inp) => (
              <AutoAnswerCard key={inp.id} input={inp} jobId={jobId!} />
            ))}
        </div>
      )}

      <AgentFilterTabs
        nodeStates={nodeStates}
        selectedNodeId={selectedNodeId}
        onSelect={handleNodeSelect}
      />

      {/* Node filter indicator */}
      {selectedNodeId && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground bg-muted/50 rounded-md px-3 py-1.5">
          <span>
            Filtering logs for: <span className="font-semibold text-foreground">{selectedNodeLabel}</span>
          </span>
          <button
            onClick={() => setSelectedNodeId(null)}
            className="ml-auto inline-flex items-center gap-1 text-xs hover:text-foreground transition-colors"
          >
            <X className="h-3 w-3" />
            Clear
          </button>
        </div>
      )}

      <LogViewer
        events={filteredEvents}
        isConnected={isConnected || jobFinished}
        isComplete={effectiveIsComplete}
        costUsd={effectiveCost}
        tokensIn={latestTokensIn}
        tokensOut={latestTokensOut}
        turns={latestTurns}
      />
    </div>
  );
}
