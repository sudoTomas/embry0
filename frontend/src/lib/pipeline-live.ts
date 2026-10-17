import type { LogEvent, PipelineGraph } from "@/lib/types";
import { eventNode } from "@/lib/wire";
import { TERMINAL_JOB_STATUSES } from "@/lib/jobStatus";

/** A graph node's live state, derived from the event log by deriveNodeStates. */
export interface NodeLiveState {
  node_id: string;
  agent_type: string;
  state: "pending" | "ready" | "running" | "completed" | "failed";
  iteration: number;
  cost_usd: number;
  turns: number;
  duration_seconds: number;
}

// RAV-1285: the backend emits the job's real pipeline graph (from the
// route_plan snapshot) but no per-node state events — the live
// signals are the node_started/node_completed/error events the log stream
// already carries. This derives NodeLiveState entries for the
// graph view from those, using each graph node's `physical_node` (the
// LangGraph node whose events represent it). The QA chain inside an
// issue-to-pr job is the special case: its one graph node is lit by qa_*
// events, not node_started/node_completed. A standalone QA job's graph
// names its own nodes, which emit node events like any other.

const QA_EVENT_TYPES = new Set([
  "qa_apps_resolved",
  "qa_subtask_started",
  "qa_subtask_finished",
  "qa_subtask_status",
]);

function physicalOf(node: PipelineGraph["nodes"][number]): string {
  return (node as { physical_node?: string }).physical_node ?? node.node_id;
}

export function deriveNodeStates(
  graph: PipelineGraph | null,
  events: LogEvent[],
  jobStatus?: string,
): Record<string, NodeLiveState> {
  if (!graph || graph.nodes.length === 0) return {};

  // Physical name → graph node ids, in declaration order (a multi-step
  // template can run several steps on the same physical node, e.g. two
  // research steps on generic_agent — starts are assigned in order).
  const byPhysical = new Map<string, string[]>();
  for (const n of graph.nodes) {
    const phys = physicalOf(n);
    byPhysical.set(phys, [...(byPhysical.get(phys) ?? []), n.node_id]);
  }
  const agentTypeById = new Map(graph.nodes.map((n) => [n.node_id, n.agent_type]));

  const states: Record<string, NodeLiveState> = {};
  const startCounts = new Map<string, number>();
  const runningNodeByPhysical = new Map<string, string>();
  const startedAtByNode = new Map<string, string>();

  const upsert = (nodeId: string, patch: Partial<NodeLiveState>) => {
    const prev = states[nodeId];
    states[nodeId] = {
      node_id: nodeId,
      agent_type: agentTypeById.get(nodeId) ?? nodeId,
      state: prev?.state ?? "pending",
      iteration: prev?.iteration ?? 1,
      cost_usd: prev?.cost_usd ?? 0,
      turns: prev?.turns ?? 0,
      duration_seconds: prev?.duration_seconds ?? 0,
      ...patch,
    };
  };

  const completeRunningQa = () => {
    const qaNode = runningNodeByPhysical.get("qa");
    if (qaNode && states[qaNode]?.state === "running") {
      upsert(qaNode, { state: "completed" });
      runningNodeByPhysical.delete("qa");
    }
  };

  for (const event of events) {
    const timestamp = "timestamp" in event ? (event.timestamp ?? "") : "";

    if (QA_EVENT_TYPES.has(event.type)) {
      const qaIds = byPhysical.get("qa");
      if (qaIds && states[qaIds[0]]?.state !== "completed") {
        if (states[qaIds[0]]?.state !== "running") {
          startedAtByNode.set(qaIds[0], timestamp);
        }
        upsert(qaIds[0], { state: "running" });
        runningNodeByPhysical.set("qa", qaIds[0]);
      }
      continue;
    }

    if (event.type === "cost_update") {
      // cost_update carries no node — attribute to whichever node is
      // currently running (the stream is single-agent-at-a-time).
      const running = [...runningNodeByPhysical.values()].at(-1);
      if (running) {
        upsert(running, { cost_usd: event.cost_usd, turns: event.num_turns });
      }
      continue;
    }

    const phys = eventNode(event);
    if (!phys) continue;

    if (event.type === "node_started") {
      // Any later stage starting means the QA chain (which emits no
      // node_completed of its own) is done.
      if (phys !== "qa") completeRunningQa();
      const ids = byPhysical.get(phys);
      if (!ids) continue;
      const count = startCounts.get(phys) ?? 0;
      // Starts map onto this physical's nodes in order; once exhausted, a
      // re-start is a retry of the last one (iteration++).
      const target = ids[Math.min(count, ids.length - 1)];
      startCounts.set(phys, count + 1);
      runningNodeByPhysical.set(phys, target);
      startedAtByNode.set(target, timestamp);
      upsert(target, {
        state: "running",
        iteration: states[target] ? (states[target].iteration ?? 1) + (states[target].state !== "pending" ? 1 : 0) : 1,
      });
    } else if (event.type === "node_completed") {
      const target = runningNodeByPhysical.get(phys);
      if (!target) continue;
      const startedAt = startedAtByNode.get(target);
      const duration =
        startedAt && timestamp
          ? Math.max(0, (new Date(timestamp).getTime() - new Date(startedAt).getTime()) / 1000)
          : 0;
      upsert(target, { state: "completed", duration_seconds: duration });
      runningNodeByPhysical.delete(phys);
    } else if (event.type === "error") {
      const target = runningNodeByPhysical.get(phys);
      if (target) {
        upsert(target, { state: "failed" });
        runningNodeByPhysical.delete(phys);
      }
    }
  }

  // A finished job leaves no node running — anything still "running" was
  // interrupted by the terminal transition (same orphan rule the job
  // detail page applies).
  if (jobStatus && TERMINAL_JOB_STATUSES.has(jobStatus)) {
    const ok = jobStatus === "completed" || jobStatus === "pr_merged" || jobStatus === "skipped";
    for (const st of Object.values(states)) {
      if (st.state === "running") {
        st.state = ok ? "completed" : "failed";
      }
    }
  }

  return states;
}
