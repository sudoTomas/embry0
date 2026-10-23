import type { LogEvent, PipelineGraph } from "@/lib/types";
import { eventNode } from "@/lib/wire";
import { isSuccessfulTerminal, isTerminalStatus } from "@/lib/jobStatus";

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
// names its own nodes, which emit node events like any other. An event whose
// `node` the graph does not know (an agent run nested inside a LangGraph node
// stamps its own name, e.g. `qa`) belongs to the graph node running at that
// moment (#90).

const QA_EVENT_TYPES = new Set([
  "qa_apps_resolved",
  "qa_subtask_started",
  "qa_subtask_finished",
  "qa_subtask_status",
  "qa_agent_transient_retry",
]);

/** The LangGraph node whose events represent a graph node. */
export function physicalOf(node: PipelineGraph["nodes"][number]): string {
  return node.physical_node ?? node.node_id;
}

/**
 * Whether a node_completed reports that its node failed: the triage and verify
 * nodes say action "failed", the onboarding nodes ok false, and a node may
 * carry an error. Completion alone is not success.
 */
export function nodeCompletedFailed(event: LogEvent): boolean {
  if (event.type !== "node_completed") return false;
  return event.action === "failed" || event.ok === false || (typeof event.error === "string" && event.error !== "");
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

  // The physical an event's own `node` stands for: itself when the graph has
  // it, else the physical running last (an unknown name is a nested agent's),
  // or undefined when nothing runs.
  const resolvePhysical = (own: string): string | undefined =>
    byPhysical.has(own) ? own : [...runningNodeByPhysical.keys()].at(-1);

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
      // Attribute to the graph node currently running on the event's own
      // physical node. A pre-contract row has no run_id and either no `node`
      // or the agent type as `node` (the old executor stamped
      // `node = agent_type`, so a reviewer row says `reviewer` while its
      // node_started says `review`); it falls back to whichever node is
      // running last (the stream was single-agent-at-a-time).
      const own = eventNode(event);
      const lastRunning = [...runningNodeByPhysical.values()].at(-1);
      const resolved = own ? resolvePhysical(own) : undefined;
      const running = own
        ? ((resolved && runningNodeByPhysical.get(resolved)) ?? (event.run_id ? undefined : lastRunning))
        : lastRunning;
      if (running) {
        // One cost_update per run, carrying that run's delta: a retried
        // node's attempts add up.
        const prev = states[running];
        upsert(running, {
          cost_usd: (prev?.cost_usd ?? 0) + event.cost_usd,
          turns: (prev?.turns ?? 0) + event.num_turns,
        });
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
      // re-start is a retry of the last one.
      const target = ids[Math.min(count, ids.length - 1)];
      startCounts.set(phys, count + 1);
      runningNodeByPhysical.set(phys, target);
      startedAtByNode.set(target, timestamp);
      // The event's `attempt` is the iteration; legacy rows without one
      // fall back to counting re-starts.
      const counted = states[target] ? states[target].iteration + (states[target].state !== "pending" ? 1 : 0) : 1;
      upsert(target, {
        state: "running",
        iteration: typeof event.attempt === "number" ? event.attempt : counted,
      });
    } else if (event.type === "node_completed") {
      const target = runningNodeByPhysical.get(phys);
      if (!target) continue;
      const startedAt = startedAtByNode.get(target);
      const duration =
        startedAt && timestamp
          ? Math.max(0, (new Date(timestamp).getTime() - new Date(startedAt).getTime()) / 1000)
          : 0;
      // A run-ending error already failed the node; completing it must not hide
      // that, and a node that reports its own failure is failed too.
      const state = states[target]?.state === "failed" || nodeCompletedFailed(event) ? "failed" : "completed";
      upsert(target, { state, duration_seconds: duration });
      runningNodeByPhysical.delete(phys);
    } else if (event.type === "error") {
      // An error naming a tool is a recoverable denial: the run goes on.
      if (event.tool_name) continue;
      // Fail the node but keep it running-registered, so the run's later
      // cost_update and node_completed still land on it.
      const resolved = resolvePhysical(phys);
      const target = resolved ? runningNodeByPhysical.get(resolved) : undefined;
      if (target) upsert(target, { state: "failed" });
    }
  }

  // A finished job leaves no node running — anything still "running" was
  // interrupted by the terminal transition (same orphan rule the job
  // detail page applies).
  if (isTerminalStatus(jobStatus)) {
    const ok = isSuccessfulTerminal(jobStatus);
    for (const st of Object.values(states)) {
      if (st.state === "running") {
        st.state = ok ? "completed" : "failed";
      }
    }
  }

  return states;
}

/**
 * The events the logs page shows for one selected graph node. Events carry the
 * LangGraph node (`review` for a `reviewer` graph node, `generic_agent` for
 * research/analysis/ops steps), so the selection is resolved to its graph
 * node's `physical_node` first. Graph nodes sharing one physical node show the
 * same events; the qa physical also collects every qa_* event; job-level events
 * (no `node`) are always kept. An event whose `node` the graph does not know (a
 * nested agent run's, e.g. `qa`) belongs to the physical running when it was
 * emitted, from its node_started to its node_completed (#90). An id the graph
 * does not know, or a selection with no graph, keeps only the job-level events.
 */
export function filterEventsForNode(
  graph: PipelineGraph | null,
  events: LogEvent[],
  selectedNodeId: string | null,
): LogEvent[] {
  if (!selectedNodeId) return events;
  const selected = graph?.nodes.find((n) => n.node_id === selectedNodeId);
  const physical = selected ? physicalOf(selected) : null;
  const known = new Set((graph?.nodes ?? []).map(physicalOf));
  let running: string | null = null;
  return events.filter((e) => {
    const node = eventNode(e);
    if (node === "") return true;
    if (known.has(node)) {
      if (e.type === "node_started") running = node;
      else if (e.type === "node_completed" && running === node) running = null;
    }
    if (physical === null) return false;
    if (node === physical) return true;
    // A graph with a qa physical owns every qa_* event, whatever node they name.
    if (known.has("qa") && QA_EVENT_TYPES.has(e.type)) return physical === "qa";
    return !known.has(node) && running === physical;
  });
}
