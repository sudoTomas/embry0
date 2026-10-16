import { useMemo, useState, useCallback } from "react";
import { useParams, Link } from "react-router";
import { useJobLogs } from "@/hooks/useJobLogs";
import { useJob } from "@/hooks/useJobs";
import { useJobInputs } from "@/hooks/useInputs";
import { deriveNodeStates } from "@/lib/pipeline-live";
import { LogViewer } from "@/components/logs/LogViewer";
import { PipelineGraphView } from "@/components/pipeline/PipelineGraphView";
import { AgentFilterTabs } from "@/components/logs/AgentFilterTabs";
import { InputForm } from "@/components/jobs/InputForm";
import { AutoAnswerCard } from "@/components/jobs/AutoAnswerCard";
import { Button } from "@/components/ui/Button";
import { ArrowLeft, X } from "lucide-react";

export function JobLogsPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const {
    events,
    isConnected,
    isComplete,
    latestCost,
    latestTokensIn,
    latestTokensOut,
    latestTurns,
    pipelineGraph,
    nodeStates,
    feedbackStates,
    pendingInputs,
  } = useJobLogs(jobId);

  // Load job data for completion detection. RAV-1285: the graph arrives on
  // the `pipeline_graph` event plan_route emits (persisted + WS-replayed,
  // so late joiners get it too); per-node live state is derived from the
  // node_started/node_completed/qa_* events the stream already carries.
  const { data: job } = useJob(jobId);
  const effectiveGraph = pipelineGraph ?? null;
  const jobFinished = job?.status === "completed" || job?.status === "failed";
  const effectiveIsComplete = isComplete || jobFinished;
  const effectiveCost = latestCost || job?.total_cost_usd || 0;

  const derivedNodeStates = useMemo(
    () => deriveNodeStates(effectiveGraph, events, job?.status),
    [effectiveGraph, events, job?.status],
  );
  // Explicit node_state events (none emitted today, but the contract exists)
  // win over derivation.
  const mergedNodeStates = useMemo(
    () => ({ ...derivedNodeStates, ...nodeStates }),
    [derivedNodeStates, nodeStates],
  );

  const { data: jobInputs } = useJobInputs(jobId ?? "");

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const handleNodeSelect = useCallback((nodeId: string | null) => {
    setSelectedNodeId(nodeId);
  }, []);

  // Filter events by selected node when click-to-filter is active
  const filteredEvents = useMemo(() => {
    if (!selectedNodeId) return events;
    return events.filter((e) => {
      const nodeId = "node_id" in e ? (e as { node_id?: string }).node_id : undefined;
      // Show events that match the selected node_id
      if (nodeId === selectedNodeId) return true;
      // Always show stream-level events (complete, stream_end, job_finished, error without node_id)
      if (!nodeId && (e.type === "complete" || e.type === "stream_end" || e.type === "job_finished" || e.type === "error")) return true;
      return false;
    });
  }, [events, selectedNodeId]);

  // Find the label for the selected node
  const selectedNodeLabel = useMemo(() => {
    if (!selectedNodeId || !effectiveGraph) return null;
    const node = effectiveGraph.nodes.find((n) => n.node_id === selectedNodeId);
    return node?.label || node?.agent_type || selectedNodeId;
  }, [selectedNodeId, effectiveGraph]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <Link to="/jobs">
          <Button variant="ghost" size="icon">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div>
          <h1 className="text-2xl font-bold">Agent Logs</h1>
          <p className="text-sm text-muted-foreground font-mono">{jobId}</p>
        </div>
        <Link to={`/jobs/${jobId}`}>
          <Button variant="outline" size="sm" className="gap-1.5">
            <ArrowLeft className="w-3 h-3" />
            Dashboard
          </Button>
        </Link>
      </div>

      {/* Pipeline Visualization */}
      <div className="rounded-lg border border-border bg-card p-2">
        {effectiveGraph ? (
          <PipelineGraphView
            graph={effectiveGraph}
            nodeStates={mergedNodeStates}
            feedbackStates={feedbackStates}
            onNodeSelect={handleNodeSelect}
          />
        ) : (
          <div className="text-center text-white/20 py-8 text-sm">
            Waiting for the pipeline graph (emitted when triage routes the
            job). Jobs that predate the live graph have none — use the{" "}
            <Link to={`/jobs/${jobId}`} className="underline hover:text-white/40">
              job detail page
            </Link>{" "}
            stage timeline instead.
          </div>
        )}
      </div>

      {/* Pending input forms */}
      {Object.values(pendingInputs).length > 0 && jobInputs && (
        <div className="space-y-3">
          {jobInputs
            .filter((inp) => inp.status === "pending" && pendingInputs[inp.input_id])
            .map((inp) => (
              <InputForm key={inp.input_id} input={inp} jobId={jobId!} />
            ))}
        </div>
      )}

      {/* Auto-answered inputs */}
      {jobInputs && jobInputs.filter((inp) => inp.status === "auto_answered" || (inp.status === "answered" && inp.auto_answer != null)).length > 0 && (
        <div className="space-y-2">
          {jobInputs
            .filter((inp) => inp.status === "auto_answered" || (inp.status === "answered" && inp.auto_answer != null))
            .map((inp) => (
              <AutoAnswerCard key={inp.input_id} input={inp} jobId={jobId!} />
            ))}
        </div>
      )}

      <AgentFilterTabs
        nodeStates={mergedNodeStates}
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
