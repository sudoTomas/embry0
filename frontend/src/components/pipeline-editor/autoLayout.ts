import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";
import { shouldUseCircular, circularLayout } from "./circularLayout";

const NODE_WIDTH = 96;
const NODE_HEIGHT = 96;

export type AutoLayoutResult = {
  nodes: Node[];
  edges: Edge[];
};

/**
 * Re-position React Flow nodes into a clean layout.
 *
 * Dispatches to `circularLayout` when the canonical 4-stage pipeline cycle
 * is detected (≥2 distinct cardinal stages — triage/develop/validate/qa);
 * otherwise falls back to dagre LR layout.
 *
 * Returns { nodes, edges } because the circular path may infer new edges in
 * the canonical pipeline order. The dagre fallback returns the existing
 * edges unchanged.
 *
 * Pure: input arrays are not mutated.
 *
 */
export function autoLayout(
  nodes: Node[],
  edges: Edge[],
  direction: "LR" | "TB" = "LR",
): AutoLayoutResult {
  if (nodes.length === 0) return { nodes: [], edges };

  if (shouldUseCircular(nodes, edges)) {
    return circularLayout(nodes, edges);
  }

  return { nodes: dagreLayout(nodes, edges, direction), edges };
}

function dagreLayout(nodes: Node[], edges: Edge[], direction: "LR" | "TB"): Node[] {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep: 60, ranksep: 100 });

  for (const n of nodes) {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const e of edges) {
    if (e.type === "feedbackEdge") continue;
    g.setEdge(e.source, e.target);
  }

  dagre.layout(g);

  return nodes.map((n) => {
    const pos = g.node(n.id);
    return {
      ...n,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
    };
  });
}
