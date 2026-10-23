import { describe, it, expect } from "vitest";
import { deriveNodeStates } from "../pipeline-live";
import type { LogEvent, PipelineGraph } from "@/lib/types";
import { loadFixture } from "./fixtures";
import {
  QA_APPS_RESOLVED,
  QA_COST_UPDATE,
  QA_INIT_COMPLETED,
  QA_INIT_STARTED,
  QA_JOB_FINISHED,
  QA_ORCHESTRATE_COMPLETED,
  QA_ORCHESTRATE_STARTED,
  QA_REPORT_COMPLETED,
  QA_REPORT_STARTED,
  QA_SUBTASK_FINISHED,
  QA_SUBTASK_PASSED,
  STANDALONE_QA_EVENTS,
  STANDALONE_QA_GRAPH,
} from "./standalone-qa-stream";

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

  it("a run-ending error (no tool_name) fails the running node", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "developer" }),
      ev({ type: "error", node: "developer", message: "boom", code: "ERR_AGENT_CRASHED" }),
    ]);
    expect(states.developer.state).toBe("failed");
  });

  it("a tool-level error (tool_name set) leaves the running node alone", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "developer" }),
      ev({ type: "error", node: "developer", run_id: "r1", message: "denied", code: "ERR_TOOL_DENIED", tool_name: "Bash" }),
    ]);
    expect(states.developer.state, "a recoverable tool denial must not fail the node").toBe("running");
  });

  it("a run-ending error keeps the node so its later cost_update still lands", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "developer" }),
      ev({ type: "error", node: "developer", run_id: "r1", message: "boom" }),
      ev({ type: "cost_update", node: "developer", run_id: "r1", cost_usd: 0.5, num_turns: 3 }),
      ev({ type: "node_completed", node: "developer", timestamp: "2026-08-07T12:00:30Z" }),
    ]);
    expect(states.developer.cost_usd, "the failed run's cost was dropped").toBe(0.5);
    expect(states.developer.state, "node_completed must not hide the failure").toBe("failed");
    expect(states.developer.duration_seconds, "node_completed after the error was dropped").toBe(30);
  });

  it("the issue-to-pr fixture: a completed job's developer is completed with both runs' cost", () => {
    const events = loadFixture("issue-to-pr");
    const graphEvent = events.find((e) => e.type === "pipeline_graph");
    const graph = graphEvent?.type === "pipeline_graph" ? graphEvent.graph : null;
    const states = deriveNodeStates(graph, events, "completed");
    expect(states.developer.state, "the fixture's tool denial failed the developer node").toBe("completed");
    expect(states.developer.cost_usd, "developer's cost_updates were dropped").toBeCloseTo(0.048 + 0.0068, 6);
    expect(states.developer.iteration).toBe(2);
    expect(states.reviewer.state).toBe("completed");
    expect(states.reviewer.cost_usd).toBeCloseTo(0.003, 6);
  });

  it("a legacy cost_update whose node is the agent type lands on the running graph node", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "review" }),
      ev({ type: "cost_update", node: "reviewer", cost_usd: 0.02, num_turns: 1 }),
    ]);
    expect(states.reviewer.cost_usd, "a pre-contract reviewer cost_update was dropped").toBe(0.02);
  });

  it("the legacy-rows fixture: the reviewer's cost lands on the reviewer graph node", () => {
    const states = deriveNodeStates(GRAPH, loadFixture("legacy-rows"));
    expect(states.reviewer.cost_usd, "the legacy reviewer cost_update was dropped").toBe(0.02);
    expect(states.developer.cost_usd, "the node-less legacy cost_update goes to the running developer").toBe(0.01);
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

  it("a re-started node counts as the one running last for an unknown-node cost_update", () => {
    const states = deriveNodeStates(GRAPH, [
      ev({ type: "node_started", node: "developer", attempt: 1 }),
      ev({ type: "node_started", node: "review", attempt: 1 }),
      ev({ type: "node_started", node: "developer", attempt: 2 }),
      ev({ type: "cost_update", node: "nested-run", run_id: "run-0001", cost_usd: 0.5, num_turns: 3 }),
    ]);
    expect(states.developer.cost_usd, "the re-started developer is the node running last").toBe(0.5);
    expect(states.reviewer.cost_usd, "the reviewer did not spend the nested run's cost").toBe(0);
  });

  it("a partial job closes its running node", () => {
    const states = deriveNodeStates(GRAPH, [ev({ type: "node_started", node: "developer" })], "partial");
    expect(states.developer.state, "partial is terminal").not.toBe("running");
  });

  it("returns empty for a missing graph", () => {
    expect(deriveNodeStates(null, [ev({ type: "node_started", node: "triage" })])).toEqual({});
  });
});

describe("deriveNodeStates on a standalone QA job (#90)", () => {
  it("the nested QA agent's cost_update lands on the orchestrate_qa node", () => {
    const states = deriveNodeStates(STANDALONE_QA_GRAPH, STANDALONE_QA_EVENTS, "completed");
    expect(states.orchestrate_qa.cost_usd, "the qa run's cost was attributed to no node").toBeCloseTo(1.602263, 6);
    expect(states.orchestrate_qa.turns, "the qa run's turns were attributed to no node").toBe(57);
    expect(states.init_orchestrator.cost_usd, "init_orchestrator spent nothing").toBe(0);
    expect(states.init_orchestrator.turns, "init_orchestrator ran no turns").toBe(0);
    expect(states.qa_report.cost_usd, "qa_report spent nothing").toBe(0);
    expect(states.qa_report.turns, "qa_report ran no turns").toBe(0);
    expect(states.init_orchestrator.state).toBe("completed");
    expect(states.orchestrate_qa.state).toBe("completed");
    expect(states.qa_report.state).toBe("completed");
  });

  it("a run-ending error from the nested QA agent fails orchestrate_qa", () => {
    const stream = [
      QA_INIT_STARTED,
      QA_INIT_COMPLETED,
      QA_ORCHESTRATE_STARTED,
      ev({ type: "error", node: "qa", run_id: "run-0001", message: "boom", code: "ERR_AGENT_CRASHED" }),
    ];
    const running = deriveNodeStates(STANDALONE_QA_GRAPH, stream);
    expect(running.orchestrate_qa.state, "the qa agent's error did not fail orchestrate_qa").toBe("failed");
    expect(running.init_orchestrator.state, "init_orchestrator is unaffected").toBe("completed");
    const completed = deriveNodeStates(STANDALONE_QA_GRAPH, [
      ...stream,
      ev({ type: "node_completed", node: "orchestrate_qa", action: "failed" }),
    ]);
    expect(completed.orchestrate_qa.state, "a node_completed reporting failure keeps it failed").toBe("failed");
  });

  it("a cost_update with an unknown node while nothing runs is attributed to no node", () => {
    const states = deriveNodeStates(STANDALONE_QA_GRAPH, [QA_INIT_STARTED, QA_INIT_COMPLETED, QA_COST_UPDATE]);
    for (const st of Object.values(states)) {
      expect(st.cost_usd, `${st.node_id} gained cost while nothing was running`).toBe(0);
      expect(st.turns, `${st.node_id} gained turns while nothing was running`).toBe(0);
    }
  });

  it("a nested agent's transient error does not outlive a node_completed that reports success", () => {
    const states = deriveNodeStates(
      STANDALONE_QA_GRAPH,
      [
        QA_INIT_STARTED,
        QA_INIT_COMPLETED,
        QA_ORCHESTRATE_STARTED,
        QA_APPS_RESOLVED,
        ev({ type: "error", node: "qa", run_id: "run-0001", message: "overloaded_error", code: "ERR_AGENT_CRASHED" }),
        ev({ type: "qa_agent_transient_retry", node: "qa", app: "web", reason: "overloaded_error" }),
        QA_COST_UPDATE,
        QA_SUBTASK_PASSED,
        QA_SUBTASK_FINISHED,
        QA_ORCHESTRATE_COMPLETED,
        QA_REPORT_STARTED,
        QA_REPORT_COMPLETED,
        QA_JOB_FINISHED,
      ],
      "completed",
    );
    expect(states.orchestrate_qa.state, "the retried run passed; node_completed action=passed is the verdict").toBe(
      "completed",
    );
    expect(states.orchestrate_qa.cost_usd, "the passing retry's cost still lands on the node").toBeCloseTo(1.602263, 6);
  });

  it("an error under the node's own name stays failed past a node_completed that reports success", () => {
    const states = deriveNodeStates(STANDALONE_QA_GRAPH, [
      QA_INIT_STARTED,
      QA_INIT_COMPLETED,
      QA_ORCHESTRATE_STARTED,
      ev({ type: "error", node: "orchestrate_qa", message: "boom", code: "ERR_AGENT_CRASHED" }),
      QA_ORCHESTRATE_COMPLETED,
    ]);
    expect(states.orchestrate_qa.state, "a run-ending error under the node's own name is sticky").toBe("failed");
  });
});
