import type { AgentState } from "@/hooks/useAgentStates";
import type { NodeLiveState } from "@/lib/pipeline-live";
import type { SpriteState } from "./sprites";

/**
 * The sprite state of one agent run (spec 3.2): a running agent whose
 * newest tool call has no result yet is using a tool; any other running
 * agent is thinking. Events are scanned from the end, so cost is bounded by
 * the current tool call's distance from the tail.
 */
export function stateFor(agent: Pick<AgentState, "status" | "events">): SpriteState {
  switch (agent.status) {
    case "pending": return "pending";
    case "completed": return "done";
    case "failed": return "failed";
  }
  const answered = new Set<string>();
  for (let i = agent.events.length - 1; i >= 0; i--) {
    const e = agent.events[i];
    if (e.type === "tool_result") answered.add(e.tool_id);
    else if (e.type === "tool_call") return answered.has(e.tool_id) ? "thinking" : "tool";
  }
  return "thinking";
}

/** The sprite state of a live graph node; the node vocabulary has no tool signal, so running is thinking. */
export function stateForNode(state: NodeLiveState["state"] | undefined): SpriteState {
  switch (state) {
    case "running": return "thinking";
    case "completed": return "done";
    case "failed": return "failed";
    default: return "pending";
  }
}
