import type { PipelineGraph } from "@/lib/types";

const SENTINELS = new Set(["__start__", "__end__"]);

/**
 * The agent type a template runs first (spec 3.3, QueuedCard): the target of
 * the edge out of the start sentinel, else the leftmost agent node, else
 * triage, where every default route begins.
 */
export function firstPartyMember(graph: PipelineGraph | undefined): string {
  if (!graph) return "triage";
  const byId = new Map(graph.nodes.map((n) => [n.node_id, n]));
  const start = graph.nodes.find((n) => n.agent_type === "__start__");
  const first = start && graph.edges.find((e) => e.source === start.node_id);
  const viaEdge = first ? byId.get(first.target) : undefined;
  if (viaEdge && !SENTINELS.has(viaEdge.agent_type)) return viaEdge.agent_type;
  const agents = graph.nodes.filter((n) => !SENTINELS.has(n.agent_type)).sort((a, b) => a.position.x - b.position.x);
  return agents[0]?.agent_type ?? "triage";
}
