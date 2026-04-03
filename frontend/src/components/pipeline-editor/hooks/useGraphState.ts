import React, { useState, useCallback } from "react";
import {
  useNodesState,
  useEdgesState,
  addEdge,
  type Node,
  type Edge,
  type Connection,
} from "@xyflow/react";
import {
  detectCycles,
  serializeGraph,
  deserializeGraph,
} from "@/lib/graph-utils";
import type { PipelineGraph, PipelineMetadata } from "@/lib/types";

export function useGraphState(initialGraph?: PipelineGraph) {
  const initial = initialGraph
    ? deserializeGraph(initialGraph)
    : { nodes: [] as Node[], edges: [] as Edge[] };
  const [nodes, setNodes, onNodesChange] = useNodesState(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initial.edges);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null);

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => {
        const newEdges = addEdge(
          { ...connection, type: "default", data: { edgeType: "flow" } },
          eds,
        );
        // Auto-detect cycles and mark back-edges as feedback
        const backEdgeIds = detectCycles(
          nodes.map((n) => ({ id: n.id })),
          newEdges.map((e) => ({
            id: e.id,
            source: e.source,
            target: e.target,
          })),
        );
        return newEdges.map((e) =>
          backEdgeIds.has(e.id)
            ? {
                ...e,
                type: "feedbackEdge",
                animated: true,
                style: { stroke: "#f87171", strokeDasharray: "6 4" },
                data: {
                  ...((e.data as Record<string, unknown>) ?? {}),
                  edgeType: "feedback",
                  loopConfig: {
                    max_loops: 3,
                    feedback_mode: "result",
                  },
                },
              }
            : e,
        );
      });
    },
    [nodes, setEdges],
  );

  const onDragOver = useCallback((event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const serialize = useCallback(
    (name?: string): PipelineGraph => {
      const metadata: PipelineMetadata = {
        created_by: "manual",
        max_total_budget_usd: 20,
        max_total_loops: 10,
      };
      return serializeGraph(nodes, edges, metadata, name);
    },
    [nodes, edges],
  );

  const loadGraph = useCallback(
    (graph: PipelineGraph) => {
      const { nodes: rfNodes, edges: rfEdges } = deserializeGraph(graph);
      setNodes(rfNodes);
      setEdges(rfEdges);
    },
    [setNodes, setEdges],
  );

  const updateNodeData = useCallback(
    (nodeId: string, data: Record<string, unknown>) => {
      setNodes((nds) =>
        nds.map((n) =>
          n.id === nodeId ? { ...n, data: { ...n.data, ...data } } : n,
        ),
      );
    },
    [setNodes],
  );

  const updateEdgeData = useCallback(
    (edgeId: string, data: Record<string, unknown>) => {
      setEdges((eds) =>
        eds.map((e) =>
          e.id === edgeId
            ? {
                ...e,
                data: {
                  ...((e.data as Record<string, unknown>) ?? {}),
                  ...data,
                },
              }
            : e,
        ),
      );
    },
    [setEdges],
  );

  return {
    nodes,
    edges,
    selectedNode,
    selectedEdge,
    onNodesChange,
    onEdgesChange,
    onConnect,
    onDragOver,
    setSelectedNode,
    setSelectedEdge,
    serialize,
    loadGraph,
    updateNodeData,
    updateEdgeData,
    setNodes,
    setEdges,
  };
}
