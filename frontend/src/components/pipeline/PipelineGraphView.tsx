import { ReactFlow, ReactFlowProvider, Background, type Node, type Edge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMemo, useCallback } from "react";
import { deserializeGraph } from "@/lib/graph-utils";
import type { PipelineGraph } from "@/lib/types";
import type { NodeLiveState } from "@/lib/pipeline-live";
import { StartEndNode } from "@/components/pipeline-editor/StartEndNode";
import { PartyNode } from "./PartyNode";

// ---------------------------------------------------------------------------
// Merge node states into React Flow data
// ---------------------------------------------------------------------------

// Exported for its test (React Flow does not lay nodes out in jsdom); Fast
// Refresh reloads this module in full when it changes, which is all it costs.
// eslint-disable-next-line react-refresh/only-export-components
export function buildFlowData(
  graph: PipelineGraph,
  nodeStates: Record<string, NodeLiveState>,
  selectedNodeId: string | null = null,
): { nodes: Node[]; edges: Edge[] } {
  const { nodes: baseNodes, edges: baseEdges } = deserializeGraph(graph);

  const nodes: Node[] = baseNodes.map((n) =>
    n.type === "startEndNode"
      ? { ...n, draggable: false, selectable: false, connectable: false }
      : {
          ...n,
          draggable: false,
          selectable: true,
          connectable: false,
          selected: n.id === selectedNodeId,
          data: {
            ...n.data,
            mode: "live",
            state: nodeStates[n.id]?.state ?? "pending",
            costUsd: nodeStates[n.id]?.cost_usd ?? 0,
            turns: nodeStates[n.id]?.turns ?? 0,
            durationSeconds: nodeStates[n.id]?.duration_seconds ?? 0,
            iteration: nodeStates[n.id]?.iteration ?? 1,
          },
        },
  );

  return { nodes, edges: baseEdges };
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface PipelineGraphViewProps {
  graph: PipelineGraph | null;
  nodeStates: Record<string, NodeLiveState>;
  onNodeSelect?: (nodeId: string | null) => void;
  /** The graph node whose run is selected on the page; the map draws it selected. */
  selectedNodeId?: string | null;
}

// ---------------------------------------------------------------------------
// Node types registry (must be stable reference)
// ---------------------------------------------------------------------------

const nodeTypes = { agentNode: PartyNode, startEndNode: StartEndNode };

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

function PipelineGraphViewInner({
  graph,
  nodeStates,
  onNodeSelect,
  selectedNodeId = null,
}: PipelineGraphViewProps) {
  const { nodes, edges } = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    return buildFlowData(graph, nodeStates, selectedNodeId);
  }, [graph, nodeStates, selectedNodeId]);

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
    <div className="h-[40vh] w-full rounded-lg overflow-hidden bg-canvas">
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
        <Background color="var(--color-grid)" gap={20} />
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
