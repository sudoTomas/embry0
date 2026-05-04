import type { Edge, Node } from "@xyflow/react";
import { categoryToStage, type CardinalStage } from "@/lib/sigils";
import { inferCanonicalEdges } from "./inferCanonicalEdges";

export const CIRCLE_RADIUS = 220;
const OUTER_RING_RATIO = 1.5;
const SENTINEL_OFFSET = 1.4;

/**
 * Cardinal angle in radians for each stage (CSS coordinates: y grows down).
 * North = -π/2, East = 0, South = π/2, West = π.
 */
const STAGE_ANGLE: Record<CardinalStage, number> = {
  triage: -Math.PI / 2, // N (top)
  develop: 0,           //  E (right)
  validate: Math.PI / 2, // S (bottom)
  qa: Math.PI,          //  W (left)
};

const ANGULAR_OFFSETS_DEG = [0, -12, 12, -20, 20, -35, 35] as const;

function nodeStage(node: Node): CardinalStage | null {
  const agentType = (node.data?.agentType as string) ?? "";
  const stage = categoryToStage(agentType);
  if (stage === "triage" || stage === "develop" || stage === "validate" || stage === "qa") {
    return stage;
  }
  return null;
}

function isSentinel(node: Node): "start" | "end" | null {
  if (node.type === "start") return "start";
  if (node.type === "end") return "end";
  return null;
}

/**
 * Returns true when at least 2 distinct cardinal stages are represented in the graph.
 */
export function canonicalCycleDetected(nodes: Node[]): boolean {
  const present = new Set<CardinalStage>();
  for (const n of nodes) {
    const s = nodeStage(n);
    if (s) present.add(s);
    if (present.size >= 2) return true;
  }
  return false;
}

/**
 * Place nodes around a circle whose cardinal points (N/E/S/W) match the
 * canonical 4-stage pipeline (triage / develop / validate / qa). Non-cardinal
 * agents go on an outer ring at 1.5×R; Start/End sentinels sit on the equator
 * just outside the circle.
 *
 * Also infers any missing edges in the canonical cycle order. Returns
 * { nodes, edges } where edges = existing + inferred (additive).
 *
 * See `docs/superpowers/specs/2026-05-04-auto-arrange-circular-design.md` §3.
 */
export function circularLayout(
  nodes: Node[],
  edges: Edge[],
): { nodes: Node[]; edges: Edge[] } {
  const stagedCount: Record<CardinalStage, number> = {
    triage: 0,
    develop: 0,
    validate: 0,
    qa: 0,
  };
  const outerNodes: Node[] = [];
  const startNodes: Node[] = [];
  const endNodes: Node[] = [];
  const cardinalNodes: { node: Node; stage: CardinalStage }[] = [];

  for (const n of nodes) {
    const sentinel = isSentinel(n);
    if (sentinel === "start") {
      startNodes.push(n);
      continue;
    }
    if (sentinel === "end") {
      endNodes.push(n);
      continue;
    }
    const stage = nodeStage(n);
    if (stage) {
      cardinalNodes.push({ node: n, stage });
    } else {
      outerNodes.push(n);
    }
  }

  const positioned: Node[] = [];

  for (const { node, stage } of cardinalNodes) {
    const baseAngle = STAGE_ANGLE[stage];
    const offsetIndex = stagedCount[stage];
    stagedCount[stage] += 1;
    const offsetDeg = ANGULAR_OFFSETS_DEG[offsetIndex] ?? 0;
    const angle = baseAngle + (offsetDeg * Math.PI) / 180;
    positioned.push({
      ...node,
      position: {
        x: Math.cos(angle) * CIRCLE_RADIUS,
        y: Math.sin(angle) * CIRCLE_RADIUS,
      },
    });
  }

  // Outer ring: distribute non-cardinal nodes around 360° starting at NE
  outerNodes.forEach((node, i) => {
    const angle = -Math.PI / 4 + (i * Math.PI) / Math.max(outerNodes.length, 4);
    positioned.push({
      ...node,
      position: {
        x: Math.cos(angle) * CIRCLE_RADIUS * OUTER_RING_RATIO,
        y: Math.sin(angle) * CIRCLE_RADIUS * OUTER_RING_RATIO,
      },
    });
  });

  for (const node of startNodes) {
    positioned.push({
      ...node,
      position: { x: -CIRCLE_RADIUS * SENTINEL_OFFSET, y: 0 },
    });
  }
  for (const node of endNodes) {
    positioned.push({
      ...node,
      position: { x: CIRCLE_RADIUS * SENTINEL_OFFSET, y: 0 },
    });
  }

  const inferred = inferCanonicalEdges(nodes, edges);
  return {
    nodes: positioned,
    edges: [...edges, ...inferred],
  };
}
