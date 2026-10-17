import { describe, it, expect } from "vitest";
import { deriveNodeStates } from "../pipeline-live";
import type { LogEvent, PipelineGraph } from "@/lib/types";

const GRAPH: PipelineGraph = {
  graph_id: "route-job-1",
  name: "full-magnum-opus",
  nodes: [
    { node_id: "triage", agent_type: "triage", physical_node: "triage", position: { x: 0, y: 0 } },
    { node_id: "developer", agent_type: "developer", physical_node: "developer", position: { x: 230, y: 0 } },
    { node_id: "reviewer", agent_type: "reviewer", physical_node: "review", position: { x: 460, y: 0 } },
    { node_id: "qa", agent_type: "qa", physical_node: "qa", position: { x: 690, y: 0 } },
  ],
  edges: [],
  metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "triage" },
};

function ev(partial: Record<string, unknown>): LogEvent {
  return { timestamp: "2026-08-07T12:00:00Z", ...partial } as unknown as LogEvent;
}

describe("deriveNodeStates (RAV-1285)", () => {
  it("maps node_started/node_completed onto graph nodes via physical_node", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "triage" }),
      ev({ type: "node_completed", node: "triage", timestamp: "2026-08-07T12:01:00Z" }),
      ev({ type: "node_started", node: "developer" }),
    ]);
    expect(states.triage.state).toBe("completed");
    expect(states.triage.duration_seconds).toBe(60);
    expect(states.developer.state).toBe("running");
    expect(states.reviewer).toBeUndefined();
  });

  it("the review physical node lights up the reviewer graph node", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "review" }),
    ]);
    expect(states.reviewer.state).toBe("running");
  });

  it("qa_* events run the qa node; a later stage start completes it", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "qa_subtask_started", app: "backend" }),
      ev({ type: "node_started", node: "finalize_output" }),
    ]);
    expect(states.qa.state).toBe("completed");
  });

  it("a qa_subtask_status event marks the qa node running", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "qa_subtask_status", app: "backend", status: "running" }),
    ]);
    expect(states.qa.state).toBe("running");
  });

  it("attributes cost_update to the running node", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "developer" }),
      ev({ type: "cost_update", cost_usd: 1.25, num_turns: 7, tokens_in: 1, tokens_out: 2 }),
    ]);
    expect(states.developer.cost_usd).toBe(1.25);
    expect(states.developer.turns).toBe(7);
  });

  it("a re-start of the same physical node is a retry (iteration bump)", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "developer" }),
      ev({ type: "node_completed", node: "developer" }),
      ev({ type: "node_started", node: "developer" }),
    ]);
    expect(states.developer.state).toBe("running");
    expect(states.developer.iteration).toBe(2);
  });

  it("terminal job status resolves still-running nodes", () => {
    const running = [ev({ type: "node_started", node: "developer" })];
    expect(deriveNodeStates(GRAPH, running, "completed").developer.state).toBe("completed");
    expect(deriveNodeStates(GRAPH, running, "failed").developer.state).toBe("failed");
    expect(deriveNodeStates(GRAPH, running, "running").developer.state).toBe("running");
  });

  it("error events fail the running node", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "developer" }),
      ev({ type: "error", node: "developer", message: "boom" }),
    ]);
    expect(states.developer.state).toBe("failed");
  });

  it("multi-step templates assign repeated physicals in order", () => {
    const graph: PipelineGraph = {
      ...GRAPH,
      nodes: [
        { node_id: "res-1", agent_type: "research", physical_node: "generic_agent", position: { x: 0, y: 0 } },
        { node_id: "res-2", agent_type: "analysis", physical_node: "generic_agent", position: { x: 230, y: 0 } },
      ],
    };
    const states = deriveNodeStates(graph, [
      ev({ type: "node_started", node: "generic_agent" }),
      ev({ type: "node_completed", node: "generic_agent" }),
      ev({ type: "node_started", node: "generic_agent" }),
    ]);
    expect(states["res-1"].state).toBe("completed");
    expect(states["res-2"].state).toBe("running");
  });

  it("a node_started with attempt 2 sets iteration 2 without counting starts", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "developer", attempt: 2 }),
    ]);
    expect(states.developer.iteration, "iteration comes from the event's attempt").toBe(2);
  });

  it("a node_started without attempt falls back to counting starts", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "developer" }),
      ev({ type: "node_completed", node: "developer" }),
      ev({ type: "node_started", node: "developer" }),
    ]);
    expect(states.developer.iteration, "legacy rows count starts").toBe(2);
  });

  it("a cost_update is attributed to its own node, not the last running one", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "developer", attempt: 1 }),
      ev({ type: "node_started", node: "review", attempt: 1 }),
      ev({ type: "cost_update", node: "developer", cost_usd: 0.5, num_turns: 3 }),
    ]);
    expect(states.developer.cost_usd, "developer's cost_update lands on developer").toBe(0.5);
    expect(states.reviewer.cost_usd, "the reviewer did not spend developer's cost").toBe(0);
  });

  it("a retried node's cost and turns sum its runs' cost_update deltas", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "developer", attempt: 1 }),
      ev({ type: "cost_update", node: "developer", cost_usd: 0.5, num_turns: 3 }),
      ev({ type: "node_completed", node: "developer", attempt: 1 }),
      ev({ type: "node_started", node: "developer", attempt: 2 }),
      ev({ type: "cost_update", node: "developer", cost_usd: 0.25, num_turns: 2 }),
    ]);
    expect(states.developer.cost_usd, "cost_update is a per-run delta; attempts add up").toBe(0.75);
    expect(states.developer.turns, "turns add up across attempts").toBe(5);
  });

  it("a partial job closes its running node", () => {
    const states = deriveNodeStates(GRAPH, [ev({ type: "node_started", node: "developer" })], "partial");
    expect(states.developer.state, "partial is terminal").not.toBe("running");
  });

  it("returns empty for a missing graph", () => {
    expect(deriveNodeStates(null, [ev({ type: "node_started", node: "triage" })])).toEqual({});
  });
});
