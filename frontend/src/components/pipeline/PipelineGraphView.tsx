import { ReactFlow, ReactFlowProvider, Background, type Node, type Edge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo, useCallback } from "react";
import { deserializeGraph, getAgentColor, getAgentCategory } from "@/lib/graph-utils";
import type {
  PipelineGraph,
  NodeStateEvent,
  FeedbackTriggeredEvent,
} from "@/lib/types";
import type { NodeProps } from "@xyflow/react";

// ---------------------------------------------------------------------------
// Custom node for live monitoring view
// ---------------------------------------------------------------------------

function LiveAgentNode({ data }: NodeProps) {
  const agentType = data.agentType as string;
  const color = getAgentColor(agentType);
  const category = getAgentCategory(agentType);
  const label = (data.label as string) || agentType;
  const state = data.state as string | undefined;
  const cost = data.costUsd as number | undefined;
  const turns = data.turns as number | undefined;
  const duration = data.durationSeconds as number | undefined;
  const iteration = data.iteration as number | undefined;

  const opacity = state === "pending" ? 0.4 : state === "completed" ? 0.8 : state === "failed" ? 0.7 : 1;
  const borderColor =
    state === "failed"
      ? "#ef4444"
      : state === "running"
        ? color
        : state === "completed"
          ? `${color}90`
          : "rgba(255,255,255,0.15)";
  const glow = state === "running" ? `0 0 24px ${color}40, 0 0 8px ${color}20` : undefined;
  const runningClass = state === "running" ? "animate-pulse-glow" : "";

  return (
    <div
      className={`rounded-lg border-2 px-3 py-2.5 min-w-[150px] bg-[#1a1d2e] ${runningClass}`}
      style={{ borderColor, opacity, boxShadow: glow }}
    >
      <div className="flex justify-between items-center">
        <div>
          <div
            className="text-[10px] uppercase tracking-wider"
            style={{ color: `${color}B0` }}
          >
            {category}
          </div>
          <div className="text-[13px] font-semibold text-slate-200 mt-0.5">
            {label}
          </div>
        </div>
        <div className="text-base ml-2">
          {state === "running" && (
            <span className="animate-spin inline-block text-amber-400">&#x27F3;</span>
          )}
          {state === "completed" && (
            <span className="text-green-400">&#x2713;</span>
          )}
          {state === "failed" && (
            <span className="text-red-400">&#x2717;</span>
          )}
        </div>
      </div>
      {(state === "completed" || state === "running") && (
        <div className="text-[10px] text-white/30 mt-1">
          ${cost?.toFixed(2) ?? "\u2014"} &bull; {turns ?? 0} turns
          {duration ? ` \u2022 ${Math.round(duration)}s` : ""}
          {iteration && iteration > 1 ? ` \u2022 iter ${iteration}` : ""}
        </div>
      )}
      {state === "running" && (
        <div className="mt-1.5 h-[3px] bg-white/10 rounded overflow-hidden">
          <div
            className="h-full rounded animate-pulse"
            style={{ width: "60%", background: `linear-gradient(90deg, ${color}, ${color}80)` }}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Merge node states + feedback into React Flow data
// ---------------------------------------------------------------------------

function buildFlowData(
  graph: PipelineGraph,
  nodeStates: Record<string, NodeStateEvent>,
  feedbackStates: Record<string, FeedbackTriggeredEvent>,
): { nodes: Node[]; edges: Edge[] } {
  const { nodes: baseNodes, edges: baseEdges } = deserializeGraph(graph);

  const nodes: Node[] = baseNodes.map((n) => ({
    ...n,
    type: "liveAgentNode",
    draggable: false,
    selectable: true,
    connectable: false,
    data: {
      ...n.data,
      state: nodeStates[n.id]?.state ?? "pending",
      costUsd: nodeStates[n.id]?.cost_usd ?? 0,
      turns: nodeStates[n.id]?.turns ?? 0,
      durationSeconds: nodeStates[n.id]?.duration_seconds ?? 0,
      iteration: nodeStates[n.id]?.iteration ?? 1,
    },
  }));

  const edges: Edge[] = baseEdges.map((e) => {
    const fb = feedbackStates[e.id];
    const isFeedbackActive = !!fb;

    if (isFeedbackActive) {
      return {
        ...e,
        animated: true,
        style: { stroke: "#f87171", strokeWidth: 2, strokeDasharray: "6 4" },
        label: `loop ${fb.iteration}/${fb.max_loops ?? "?"}`,
        labelStyle: {
          fill: "#f87171",
          fontSize: 10,
          fontWeight: 600,
        },
        labelBgStyle: {
          fill: "#1a1d2e",
          fillOpacity: 0.9,
        },
        labelBgPadding: [4, 2] as [number, number],
        labelShowBg: true,
      };
    }

    return e;
  });

  return { nodes, edges };
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface PipelineGraphViewProps {
  graph: PipelineGraph | null;
  nodeStates: Record<string, NodeStateEvent>;
  feedbackStates: Record<string, FeedbackTriggeredEvent>;
  onNodeSelect?: (nodeId: string | null) => void;
}

// ---------------------------------------------------------------------------
// Node types registry (must be stable reference)
// ---------------------------------------------------------------------------

const nodeTypes = { liveAgentNode: LiveAgentNode };

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

function PipelineGraphViewInner({
  graph,
  nodeStates,
  feedbackStates,
  onNodeSelect,
}: PipelineGraphViewProps) {
  const { nodes, edges } = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    return buildFlowData(graph, nodeStates, feedbackStates);
  }, [graph, nodeStates, feedbackStates]);

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      onNodeSelect?.(node.id);
    },
    [onNodeSelect],
  );

  const onPaneClick = useCallback(() => {
    onNodeSelect?.(null);
  }, [onNodeSelect]);

  if (!graph) {
    return (
      <div className="flex items-center justify-center h-[200px] text-sm text-muted-foreground">
        Waiting for pipeline graph...
      </div>
    );
  }

  return (
    <div className="h-[40vh] w-full rounded-lg overflow-hidden bg-[#0f1120]">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
        fitView
        fitViewOptions={{ padding: 0.3 }}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={true}
        panOnDrag={true}
        zoomOnScroll={true}
        minZoom={0.3}
        maxZoom={1.5}
      >
        <Background color="#1e2040" gap={20} />
      </ReactFlow>
    </div>
  );
}

export function PipelineGraphView(props: PipelineGraphViewProps) {
  return (
    <ReactFlowProvider>
      <PipelineGraphViewInner {...props} />
    </ReactFlowProvider>
  );
}
