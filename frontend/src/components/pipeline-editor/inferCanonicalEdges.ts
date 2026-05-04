import type { Edge, Node } from "@xyflow/react";
import { categoryToStage, type CardinalStage } from "@/lib/sigils";

/**
 * Canonical pipeline order for the geodesic-identity 4-stage cycle.
 * Stage-name aliases (developer ≡ develop, review ≡ validate) are resolved
 * via categoryToStage at lookup time.
 *
 * See `docs/superpowers/specs/2026-05-04-auto-arrange-circular-design.md` §3.3.
 */
const CANONICAL_ORDER: readonly CardinalStage[] = [
  "triage",
  "develop",
  "validate",
  "qa",
] as const;

function nodeStage(node: Node): CardinalStage | null {
  const agentType = (node.data?.agentType as string) ?? "";
  const stage = categoryToStage(agentType);
  if (stage === "triage" || stage === "develop" || stage === "validate" || stage === "qa") {
    return stage;
  }
  return null;
}

function nextStageInCycle(stage: CardinalStage): CardinalStage {
  const i = CANONICAL_ORDER.indexOf(stage);
  return CANONICAL_ORDER[(i + 1) % CANONICAL_ORDER.length];
}

/**
 * Returns ONLY the new edges that should be added to wire up the canonical
 * 4-stage cycle (triage → develop → validate → qa → triage).
 *
 * Rules:
 * - Only stages with at least one node participate.
 * - For each consecutive pair (X, Y) in the canonical order, if no existing
 *   edge connects ANY X-stage node to ANY Y-stage node, create one edge from
 *   the first X-node to the first Y-node (deterministic).
 * - Existing edges are never removed.
 * - Created edges carry data: { inferred: true } so the editor can mark them.
 *
 * Returns [] when fewer than 2 distinct cardinal stages are represented.
 */
export function inferCanonicalEdges(nodes: Node[], existingEdges: Edge[]): Edge[] {
  const stagedNodes = new Map<CardinalStage, Node[]>();
  for (const n of nodes) {
    const s = nodeStage(n);
    if (!s) continue;
    const arr = stagedNodes.get(s) ?? [];
    arr.push(n);
    stagedNodes.set(s, arr);
  }

  if (stagedNodes.size < 2) return [];

  const newEdges: Edge[] = [];
  for (const stage of CANONICAL_ORDER) {
    const xs = stagedNodes.get(stage);
    if (!xs?.length) continue;
    const next = nextStageInCycle(stage);
    const ys = stagedNodes.get(next);
    if (!ys?.length) continue;

    const xIds = new Set(xs.map((n) => n.id));
    const yIds = new Set(ys.map((n) => n.id));
    const alreadyConnected = existingEdges.some(
      (e) => xIds.has(e.source) && yIds.has(e.target),
    );
    if (alreadyConnected) continue;

    newEdges.push({
      id: `inferred-${xs[0].id}-${ys[0].id}`,
      source: xs[0].id,
      target: ys[0].id,
      data: { inferred: true },
    });
  }

  return newEdges;
}
