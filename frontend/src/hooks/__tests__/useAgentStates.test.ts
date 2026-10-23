import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { useAgentStates } from "../useAgentStates";
import type { LogEvent, PipelineGraph } from "@/lib/types";
import { loadFixture, type FixtureName } from "@/lib/__tests__/fixtures";
import { QA_AGENT_STARTED, STANDALONE_QA_EVENTS, STANDALONE_QA_GRAPH } from "@/lib/__tests__/standalone-qa-stream";

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

  it("agentless nodes: every graph node of a completed QA job keeps one completed card", () => {
    const { events, graph } = fixtureWithGraph("qa");
    const { agents } = run(events, "completed", graph);
    expect(
      agents.map((a) => [a.node, a.status]),
      "a node with no agent run must keep a card once it has started",
    ).toEqual(graph.nodes.map((n) => [n.physical_node ?? n.node_id, "completed"]));
    expect(agents.every((a) => a.toolCalls.length === 0)).toBe(true);
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

describe("a run nested inside a graph node (#104)", () => {
  // The standalone QA job's agent runs inside the orchestrate_qa LangGraph
  // node and stamps `node: "qa"`, a name the graph does not know. The run is
  // that node's work: the node must not get a second card of its own on top,
  // carrying the same cost_update once more.
  it("is its graph node's one card, with the run's cost counted once", () => {
    const { agents } = run(STANDALONE_QA_EVENTS, "completed", STANDALONE_QA_GRAPH);
    expect(agents.map((a) => a.runId), "the orchestrate_qa node's work is one card: its run").toEqual([
      "run-0001", "node:init_orchestrator", "node:qa_report",
    ]);
    const qaRun = agents.find((a) => a.runId === "run-0001")!;
    expect(qaRun.node, "a nested run belongs to the graph node running when it started").toBe("orchestrate_qa");
    expect(qaRun.agent).toBe("qa");
    expect([qaRun.status, qaRun.costUsd, qaRun.turnCount], "the run's own rows still reach its card").toEqual(["completed", 1.602263, 0]);
    expect(agents.reduce((sum, a) => sum + a.costUsd, 0), "the run's one cost_update is counted once").toBeCloseTo(1.602263, 6);
    expect(qaRun.summary, "neither the node's action nor its closing progress row may replace the outcome agent_completed set").toBe("All acceptance criteria passed.");
    expect(qaRun.events.slice(0, 7).map((e) => e.type), "the run's record starts with its node's own rows").toEqual([
      "node_started", "qa_apps_resolved", "qa_subtask_started", "qa_subtask_status", "progress", "progress", "agent_started",
    ]);
  });

  it("the sub-task's progress rows before the run open no run", () => {
    const upToRun = STANDALONE_QA_EVENTS.slice(0, STANDALONE_QA_EVENTS.indexOf(QA_AGENT_STARTED));
    const { agents } = run(upToRun, "running", STANDALONE_QA_GRAPH);
    expect(agents.map((a) => a.runId), "an unknown-node row before any run is nobody's").toEqual([
      "node:init_orchestrator", "node:orchestrate_qa", "pending:qa_report",
    ]);
  });

  it("a progress row after the run completed leaves its outcome alone; one while it runs is its summary", () => {
    const progress = (message: string) => ({ type: "progress", node: "developer", message, timestamp: t });
    const completed = { type: "agent_completed", node: "developer", run_id: "r1", is_error: false, output: "done", cost_usd: 0.01, duration_ms: 1,
      error_code: null, error_message: "", tools_called: {}, policy_denials: {}, input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_creation_tokens: 0, timestamp: t };
    const { agents } = run([...started("developer", "r1"), progress("working"), completed, progress("wrapping up the node")]);
    expect(agents[0].summary, "a finished run's summary is its outcome").toBe("done");
    expect(run([...started("developer", "r1"), progress("working")]).agents[0].summary, "a running run's summary follows progress").toBe("working");
  });

  it("the node's run-less node_completed fills in a summary only for a run that has none", () => {
    const { agents } = run([
      ...started("developer", "r1"),
      { type: "node_completed", node: "developer", action: "proceed", timestamp: t },
    ]);
    expect(agents[0].summary, "a run with no outcome of its own takes the node's action").toBe("proceed");
  });

  it("a routing action never replaces an agent's own summary: triage keeps its output, a silent run takes the action", () => {
    const completed = (output: string) => ({
      type: "agent_completed", node: "triage", agent: "triage", run_id: "r1", is_error: false, output, cost_usd: 0.01, duration_ms: 1,
      error_code: null, error_message: "", tools_called: {}, policy_denials: {}, input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_creation_tokens: 0, timestamp: t,
    });
    const routed = { type: "node_completed", node: "triage", action: "ask_user", timestamp: t };
    const withOutput = run([...started("triage", "r1"), completed("Needs the owner's call on the schema."), routed]);
    expect(withOutput.agents[0].summary, "the agent's own output outranks the routing label").toBe("Needs the owner's call on the schema.");
    const silent = run([...started("triage", "r1"), completed(""), routed]);
    expect(silent.agents[0].summary, "a run with no output shows where the node routed").toBe("ask_user");
  });

  it("a node nesting several runs: each keeps its own outcome, the node card carries the verdict", () => {
    // Two apps: orchestrate_qa fans out one run per app. The web run fails,
    // the api run passes, the node's own node_completed says failed overall.
    const qa = (type: string, fields: Record<string, unknown>) => ({ type, node: "qa", timestamp: t, ...fields });
    const cost = (runId: string, cost_usd: number) => qa("cost_update", {
      run_id: runId, cost_usd, num_turns: 3, duration_ms: 1, duration_api_ms: 1, tokens_in: 1, tokens_out: 1, cache_read_tokens: 0, cache_creation_tokens: 0,
    });
    const completed = (runId: string, cost_usd: number, extra: Record<string, unknown>) => qa("agent_completed", {
      run_id: runId, agent: "qa", cost_usd, duration_ms: 1, tools_called: {}, policy_denials: {}, input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_creation_tokens: 0, error_code: null, error_message: "", output: "", is_error: false, ...extra,
    });
    const { agents } = run([
      { type: "node_started", node: "init_orchestrator", attempt: 1, timestamp: t },
      { type: "node_completed", node: "init_orchestrator", timestamp: t },
      { type: "node_started", node: "orchestrate_qa", attempt: 1, timestamp: t },
      qa("qa_apps_resolved", { apps: ["web", "api"] }),
      qa("agent_started", { agent: "qa", run_id: "run-web", model: "m", max_turns: 10 }),
      qa("agent_started", { agent: "qa", run_id: "run-api", model: "m", max_turns: 10 }),
      cost("run-web", 0.5),
      completed("run-web", 0.5, { is_error: true, error_code: "ERR_QA_FAILED", error_message: "web: criterion 2 failed" }),
      cost("run-api", 0.25),
      completed("run-api", 0.25, { output: "api: all passed" }),
      { type: "node_completed", node: "orchestrate_qa", action: "failed", timestamp: t },
      { type: "node_started", node: "qa_report", timestamp: t },
      { type: "node_completed", node: "qa_report", timestamp: t },
    ], "failed", STANDALONE_QA_GRAPH);
    expect(agents.map((a) => [a.runId, a.status, a.summary]), "the passing run must not wear the node's verdict").toEqual([
      ["run-web", "failed", "web: criterion 2 failed"],
      ["run-api", "completed", "api: all passed"],
      ["node:init_orchestrator", "completed", ""],
      ["node:orchestrate_qa", "failed", ""],
      ["node:qa_report", "completed", ""],
    ]);
    expect(agents.map((a) => a.costUsd), "the node card carries no cost of its own").toEqual([0.5, 0.25, 0, 0, 0]);
    expect(agents.find((a) => a.runId === "node:orchestrate_qa")!.turnCount).toBe(0);
  });

  it("moves a run only onto a node the graph has", () => {
    const nestedUnder = (host: string) => [
      { type: "node_started", node: host, attempt: 1, timestamp: t },
      { type: "agent_started", node: "qa", agent: "qa", run_id: "run-0001", model: "m", max_turns: 10, timestamp: t },
    ];
    expect(run(nestedUnder("init"), "running", STANDALONE_QA_GRAPH).agents[0].node, "a host the graph does not know is not a graph node").toBe("qa");
    const empty = { ...STANDALONE_QA_GRAPH, nodes: [] };
    expect(run(nestedUnder("orchestrate_qa"), "running", empty).agents[0].node, "an empty graph moves nothing").toBe("qa");
    expect(run(nestedUnder("orchestrate_qa"), "running", STANDALONE_QA_GRAPH).agents[0].node).toBe("orchestrate_qa");
  });

  it("keeps its own node name when the graph is not known yet", () => {
    const { agents } = run(STANDALONE_QA_EVENTS, "completed", null);
    expect(agents.map((a) => [a.runId, a.node]), "no graph, no resolution and no graph cards").toEqual([["run-0001", "qa"]]);
  });

  it("a run whose node the graph knows is unchanged", () => {
    const { events, graph } = fixtureWithGraph("issue-to-pr");
    const { agents } = run(events, "completed", graph);
    expect(agents.map((a) => [a.runId, a.node])).toEqual([
      ["run-fixture-1", "triage"], ["run-fixture-2", "developer"], ["run-fixture-3", "developer"], ["run-fixture-4", "review"],
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
