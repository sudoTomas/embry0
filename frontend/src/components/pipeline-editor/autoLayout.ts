import dagre from '@dagrejs/dagre';
import type { Edge, Node } from '@xyflow/react';

const NODE_WIDTH = 240;
const NODE_HEIGHT = 80;

/**
 * Re-position React Flow nodes into a clean directed-graph layout via dagre.
 *
 * Pure function: returns a new node array; the input is not mutated.
 * Edges with type === 'feedbackEdge' are excluded from the layout calculation
 * (so cycles do not collapse the main flow), but the edges array itself is
 * not modified — the caller renders all edges as before.
 */
export function autoLayout(
  nodes: Node[],
  edges: Edge[],
  direction: 'LR' | 'TB' = 'LR',
): Node[] {
  if (nodes.length === 0) return [];

  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep: 60, ranksep: 100 });

  for (const n of nodes) {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const e of edges) {
    if (e.type === 'feedbackEdge') continue;
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
