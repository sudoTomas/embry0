import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";
import type { AgentTaskBlockedBy, AgentTaskStatus } from "@/api/agent";

const NODE_WIDTH = 160;
const NODE_HEIGHT = 56;

export type BlockedNodeData = {
  label: string;
  status: AgentTaskStatus | "selected";
};

export type BlockedByGraph = {
  nodes: Node<BlockedNodeData>[];
  edges: Edge[];
};

/**
 * Build a ReactFlow `{ nodes, edges }` for the selected task's blocked-by
 * graph. The selected task is the root (right-most in LR layout); each
 * blocker is a child with an edge pointing into the root. Positions are
 * computed by dagre so the caller renders directly.
 */
export function buildBlockedByGraph(
  data: AgentTaskBlockedBy,
  rootLabel: string,
): BlockedByGraph {
  const rawNodes: Node<BlockedNodeData>[] = [
    {
      id: data.id,
      type: "blockedNode",
      position: { x: 0, y: 0 },
      data: { label: rootLabel, status: "selected" },
    },
    ...data.blocked_by.map<Node<BlockedNodeData>>((b) => ({
      id: b.id,
      type: "blockedNode",
      position: { x: 0, y: 0 },
      data: { label: b.title ?? b.id, status: b.status },
    })),
  ];

  const edges: Edge[] = data.blocked_by.map((b) => ({
    id: `${b.id}->${data.id}`,
    source: b.id,
    target: data.id,
  }));

  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "LR", nodesep: 24, ranksep: 80 });

  for (const n of rawNodes) {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const e of edges) {
    g.setEdge(e.source, e.target);
  }
  dagre.layout(g);

  const nodes = rawNodes.map((n) => {
    const pos = g.node(n.id);
    return {
      ...n,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
    };
  });

  return { nodes, edges };
}
