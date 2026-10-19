import type { AgentState } from "@/hooks/useAgentStates";
import type { PipelineGraph } from "@/lib/types";
import { physicalOf } from "@/lib/pipeline-live";

/** A card with events of its own is an agent run; the rest are graph steps. */
const isRun = (a: AgentState) => a.events.length > 0;

/**
 * The cards in the order the job travels them, for the job's line and the
 * Pipeline tab alike. useAgentStates lists runs in the order they started,
 * then the graph steps that have no run (not started yet, or run without an
 * agent, like onboarding's checks) in the graph's order. Each of those goes
 * back to its place: before the first run of a later graph step. Runs keep
 * their order, so a loop back from review to the developer reads as the way
 * the job went.
 */
export function inPipelineOrder(agents: AgentState[], graph: PipelineGraph | null): AgentState[] {
  if (!graph || graph.nodes.length === 0) return agents;
  const place = new Map<string, number>();
  graph.nodes.forEach((n, i) => {
    const physical = physicalOf(n);
    if (!place.has(physical)) place.set(physical, i);
  });
  const ordered = agents.filter(isRun);
  for (const step of agents.filter((a) => !isRun(a))) {
    const at = place.get(step.node) ?? Infinity;
    const before = ordered.findIndex((a) => isRun(a) && (place.get(a.node) ?? -1) > at);
    ordered.splice(before < 0 ? ordered.length : before, 0, step);
  }
  return ordered;
}
