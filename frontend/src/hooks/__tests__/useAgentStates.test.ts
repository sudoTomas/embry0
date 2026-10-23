import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { useAgentStates } from "../useAgentStates";
import type { LogEvent, PipelineGraph } from "@/lib/types";
import { loadFixture, type FixtureName } from "@/lib/__tests__/fixtures";

const t = "2026-10-15T00:00:00Z";

function run(events: unknown[], jobStatus?: string, graph?: PipelineGraph | null) {
  return renderHook(() => useAgentStates(events as LogEvent[], jobStatus, graph)).result.current;
}

function fixtureWithGraph(name: FixtureName): { events: LogEvent[]; graph: PipelineGraph } {
  const events = loadFixture(name);
  const graphEvent = events.find((e) => e.type === "pipeline_graph");
  if (graphEvent?.type !== "pipeline_graph") throw new Error(`${name} has no pipeline_graph`);
  return { events, graph: graphEvent.graph };
}

const started = (node: string, runId: string, attempt = 1) => [
  { type: "node_started", node, attempt, timestamp: t },
  { type: "agent_started", node, agent: node, run_id: runId, model: "claude-sonnet-5-5", max_turns: 10, timestamp: t },
];

const call = (runId: string, id: string, extra: Record<string, unknown> = {}) => ({
  type: "tool_call", node: "developer", run_id: runId, tool_id: id, tool_name: "Bash", summary: `run ${id}`, tool_input: {}, timestamp: t, ...extra,
});

const result = (runId: string, id: string, content = "ok", extra: Record<string, unknown> = {}) => ({
  type: "tool_result", node: "developer", run_id: runId, tool_id: id, content, is_error: false, timestamp: t, ...extra,
});

describe("useAgentStates", () => {
  it("keys agents by run_id so a retried node shows both attempts", () => {
    const { agents } = run([
      ...started("developer", "r1", 1),
      call("r1", "a1"),
      ...started("developer", "r2", 2),
      call("r2", "a2"),
    ]);
    const dev = agents.filter((a) => a.node === "developer");
    expect(dev, "a retry must be a second card, not a replacement").toHaveLength(2);
    expect(dev.map((a) => a.runId)).toEqual(["r1", "r2"]);
    expect(dev.map((a) => a.attempt)).toEqual([1, 2]);
    expect(dev[0].retryLabel).toBeUndefined();
    expect(dev[1].retryLabel).toBe("attempt 2");
    expect(dev[0].toolCalls.map((c) => c.tool_id), "the first attempt keeps its own tool calls").toEqual(["a1"]);
    expect(dev[1].toolCalls.map((c) => c.tool_id)).toEqual(["a2"]);
  });

  it("pairs tool results by tool_id within the run", () => {
    const { agents } = run([...started("developer", "r1"), call("r1", "a"), result("r1", "a", "ok")]);
    expect(agents[0].toolCalls[0].result, "the result must pair with its call").toBe("ok");
  });

  it("nests a subagent's calls under the parent Task call", () => {
    const { agents } = run([
      ...started("developer", "r1"),
      call("r1", "task", { tool_name: "Task", summary: "Explore the code" }),
      { type: "subagent_start", node: "developer", run_id: "r1", tool_id: "task", name: "Explore", timestamp: t },
      call("r1", "s1", { tool_name: "Grep", parent_tool_use_id: "task" }),
      result("r1", "s1", "found", { parent_tool_use_id: "task" }),
      { type: "subagent_end", node: "developer", run_id: "r1", tool_id: "task", is_error: false, timestamp: t },
      result("r1", "task", "summary"),
    ]);
    const [dev] = agents;
    expect(dev.toolCalls, "the subagent's call must not be a top-level entry").toHaveLength(1);
    const [task] = dev.toolCalls;
    expect(task.children, "the subagent's call must nest under its parent").toHaveLength(1);
    expect(task.children[0].result, "a nested call's result must pair").toBe("found");
    expect(task.subagent?.name, "subagent_start must name the parent's subagent").toBe("Explore");
    expect(task.subagent?.is_error).toBe(false);
    expect(dev.toolCallCount, "nested calls still count").toBe(2);
  });

  it("keeps a child whose parent is unknown at the top level", () => {
    const { agents } = run([...started("developer", "r1"), call("r1", "orphan", { parent_tool_use_id: "gone" })]);
    expect(agents[0].toolCalls.map((c) => c.tool_id), "an orphaned child must not be dropped").toEqual(["orphan"]);
    expect(agents[0].toolCalls[0].children).toEqual([]);
  });

  it("pending agents come from the graph, not a literal list", () => {
    const graph = {
      graph_id: "g",
      name: "g",
      nodes: [
        { node_id: "triage", agent_type: "triage", physical_node: "triage", position: { x: 0, y: 0 } },
        { node_id: "research", agent_type: "research", physical_node: "generic_agent", position: { x: 0, y: 0 } },
      ],
      edges: [],
      metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "triage" },
    } as unknown as PipelineGraph;
    const events = started("triage", "r1");

    const withGraph = run(events, "running", graph);
    expect(
      withGraph.agents.map((a) => [a.node, a.agent, a.status]),
      "pending cards must be the graph's unstarted nodes",
    ).toEqual([
      ["triage", "triage", "running"],
      ["generic_agent", "research", "pending"],
    ]);

    const noGraph = run(events, "running", null);
    expect(noGraph.agents.filter((a) => a.status === "pending"), "no graph means no pending cards").toEqual([]);
  });

  it("a run-ending error marks the run failed with its message and code", () => {
    const { agents } = run([
      ...started("developer", "r1"),
      { type: "error", node: "developer", run_id: "r1", message: "Agent crashed", code: "ERR_AGENT_CRASHED", timestamp: t },
    ]);
    expect(agents[0].status).toBe("failed");
    expect(agents[0].summary).toBe("Agent crashed");
    expect(agents[0].errorCode, "the error's code must reach the card").toBe("ERR_AGENT_CRASHED");
  });

  it("a tool-level error (tool_name set) leaves the run running", () => {
    const { agents } = run([
      ...started("developer", "r1"),
      { type: "error", node: "developer", run_id: "r1", message: "Tool denied", code: "ERR_TOOL_DENIED", tool_name: "Bash", timestamp: t },
    ]);
    expect(agents[0].status, "a recoverable tool denial must not fail the card").toBe("running");
    expect(agents[0].errorCode).toBeUndefined();
  });

  it("prUrl comes from pr_created", () => {
    const { prUrl } = run([
      { type: "pr_created", node: "finalize_output", pr_url: "https://forge.example.test/o/r/pull/7", branch: "b", timestamp: t },
    ]);
    expect(prUrl, "pr_created is the only event carrying the PR URL").toBe("https://forge.example.test/o/r/pull/7");
  });

  it("interrupt data comes from the event's summary", () => {
    const { interruptData } = run([
      {
        type: "interrupt",
        node: "review",
        reason: "awaiting_human_merge",
        summary: { message: "Approve?", retry_count: 2, options: ["approve"], latest_review: "nit" },
        timestamp: t,
      },
    ]);
    expect(interruptData, "only the fields the paused banner reads").toEqual({
      reason: "awaiting_human_merge",
      retry_count: 2,
      latest_review: "nit",
    });
  });

  it("a review error standing in for the latest review is labelled as one", () => {
    const { interruptData } = run([
      { type: "interrupt", node: "review", reason: "review_failed", summary: { review_error: "reviewer timed out" }, timestamp: t },
    ]);
    expect(interruptData?.latest_review, "a review error must not read as a review").toBe("Review error: reviewer timed out");
  });

  it("a legacy event without run_id attaches to the latest state for its node", () => {
    const { agents } = run([
      ...started("developer", "r1"),
      { type: "text", node: "developer", text: "hello", timestamp: t },
    ]);
    expect(agents[0].textBlocks, "a run-less event must attach to its node's latest run").toHaveLength(1);
  });

  it("agent_completed closes the run with its output and marks is_error", () => {
    const completed = (extra: Record<string, unknown>) => ({
      type: "agent_completed", node: "developer", run_id: "r1", cost_usd: 0.01, duration_ms: 1000, output: "",
      error_code: null, error_message: "", tools_called: {}, policy_denials: {}, input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_creation_tokens: 0, timestamp: t, ...extra,
    });

    const failed = run([...started("developer", "r1"), completed({ is_error: true, error_code: "ERR_X", error_message: "bad" })]);
    expect(failed.agents[0].status, "is_error must fail the run").toBe("failed");
    expect(failed.agents[0].errorCode).toBe("ERR_X");
    expect(failed.agents[0].summary).toBe("bad");

    const ok = run([...started("developer", "r1"), completed({ is_error: false, output: "done" })]);
    expect(ok.agents[0].status, "agent_completed must close the run").toBe("completed");
    expect(ok.agents[0].summary).toBe("done");
  });

  it("the issue-to-pr fixture yields four runs in node order with the fixture's costs", () => {
    const { agents } = run(loadFixture("issue-to-pr"));
    expect(agents.map((a) => a.runId)).toEqual(["run-fixture-1", "run-fixture-2", "run-fixture-3", "run-fixture-4"]);
    expect(agents.map((a) => a.node)).toEqual(["triage", "developer", "developer", "review"]);
    expect(agents.map((a) => a.costUsd)).toEqual([0.0042, 0.048, 0.0068, 0.003]);
    expect(agents[1].toolCalls.map((c) => c.tool_name), "Grep must nest under Task").toEqual(["Task", "Bash", "Edit"]);
    expect(agents[1].toolCalls[0].children).toHaveLength(1);
  });

  it("the issue-to-pr fixture's retried developer is two cards, each with its own calls", () => {
    const { agents } = run(loadFixture("issue-to-pr"));
    const dev = agents.filter((a) => a.node === "developer");
    expect(dev, "the retried developer must be two cards").toHaveLength(2);
    expect(dev.map((a) => a.attempt)).toEqual([1, 2]);
    expect(dev[1].retryLabel).toBe("attempt 2");
    expect(dev[1].toolCalls.map((c) => c.input), "attempt 2 keeps its own call").toEqual(["pytest -q"]);
  });

  it("the legacy-rows fixture: the reviewer's rows (node = agent type) attach to its card", () => {
    const { agents } = run(loadFixture("legacy-rows"));
    const reviewer = agents.find((a) => a.node === "review");
    expect(reviewer, "no reviewer card").toBeDefined();
    expect(reviewer!.toolCalls, "the legacy reviewer's tool call did not attach").toMatchObject([
      { tool_id: "legacy-2", input: "cli.py", result: "import argparse" },
    ]);
    expect(reviewer!.costUsd, "the legacy reviewer's cost did not attach").toBe(0.02);
  });

  it("a pre-contract agent_started without run_id still gets a card for its node", () => {
    const { agents } = run(loadFixture("legacy-rows"));
    expect(agents.map((a) => a.node), "a pre-contract job must not lose its agent cards").toEqual(["developer", "review"]);
    expect(agents[0].toolCalls).toMatchObject([{ tool_id: "legacy-1", input: "ls -la", result: "total 0" }]);
  });

  it("agentless nodes: a completed QA job's init and report nodes keep one completed card each", () => {
    // Only orchestrate_qa hosts an agent run (the QA agent nested inside it,
    // #102); the other two nodes run no agent and must still keep a card once
    // started. How many cards the one run yields is #104's assertion, not this one's.
    const { events, graph } = fixtureWithGraph("qa");
    const { agents } = run(events, "completed", graph);
    for (const node of ["init_orchestrator", "qa_report"]) {
      const cards = agents.filter((a) => a.node === node);
      expect(cards.map((a) => a.status), `${node} must keep exactly one completed card`).toEqual(["completed"]);
      expect(cards[0].toolCalls, `${node} ran no agent`).toHaveLength(0);
    }
    expect(
      agents.filter((a) => a.node === "orchestrate_qa").map((a) => a.status),
      "orchestrate_qa keeps a completed card",
    ).toEqual(["completed"]);
  });

  it("agentless nodes: a running QA node's card is running, the next one pending", () => {
    const { events, graph } = fixtureWithGraph("qa");
    const upToOrchestrate = events.slice(0, events.findIndex((e) => e.type === "qa_apps_resolved"));
    const { agents } = run(upToOrchestrate, "running", graph);
    expect(agents.map((a) => [a.node, a.status]), "a started agentless node vanished").toEqual([
      ["init_orchestrator", "completed"],
      ["orchestrate_qa", "running"],
      ["qa_report", "pending"],
    ]);
  });

  it("agentless nodes: onboarding's non-agent nodes have cards, its agent node only its run", () => {
    const { events, graph } = fixtureWithGraph("onboarding");
    const { agents } = run(events, "completed", graph);
    for (const n of graph.nodes) {
      const cards = agents.filter((a) => a.node === (n.physical_node ?? n.node_id));
      expect(cards, `${n.node_id} must have exactly one card`).toHaveLength(1);
      expect(cards[0].status).toBe("completed");
    }
    expect(agents.find((a) => a.node === "analyze")?.runId, "the analyze card is its run").toBe("run-fixture-onboard-1");
  });

  it("agentless nodes: nodes with runs get no extra card", () => {
    const { events, graph } = fixtureWithGraph("issue-to-pr");
    const { agents } = run(events, "completed", graph);
    expect(agents.map((a) => a.runId), "a node with a run must not also get a graph card").toEqual([
      "run-fixture-1", "run-fixture-2", "run-fixture-3", "run-fixture-4",
    ]);
  });
});

describe("pausedForInput", () => {
  const ts = "2026-10-20T12:00:00Z";
  const base = loadFixture("issue-to-pr").filter((e) => e.type !== "job_finished");

  it("is true after agent_paused_for_input with no later run event", () => {
    const events = [...base, { type: "agent_paused_for_input", count: 1, timestamp: ts, run_id: "r-x", node: "developer", agent: "developer" } as LogEvent];
    const { result } = renderHook(() => useAgentStates(events, "running", null));
    expect(result.current.pausedForInput, "a pause the poll has not seen yet must show").toBe(true);
  });

  it("is false once the run goes on or the job ends", () => {
    const paused = { type: "agent_paused_for_input", count: 1, timestamp: ts, run_id: "r-x", node: "developer", agent: "developer" } as LogEvent;
    const resumed = [...base, paused, { type: "agent_started", model: "m", max_turns: 1, timestamp: ts, run_id: "r-y", node: "developer", agent: "developer" } as LogEvent];
    expect(renderHook(() => useAgentStates(resumed, "running", null)).result.current.pausedForInput).toBe(false);
    const finished = [...base, paused, { type: "job_finished", status: "completed", error_code: null, error_message: null, pr_url: null, total_cost_usd: 0, timestamp: ts } as LogEvent];
    expect(renderHook(() => useAgentStates(finished, "completed", null)).result.current.pausedForInput).toBe(false);
  });

  it("is false with no pause at all", () => {
    expect(renderHook(() => useAgentStates(base, "running", null)).result.current.pausedForInput).toBe(false);
  });
});
