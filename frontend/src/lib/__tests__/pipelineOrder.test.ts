// The order a job travels its stages, shared by the job's line and the
// Pipeline tab's cards. The cards come from useAgentStates, which lists runs
// in the order they started and then the graph steps with no run.
import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { useAgentStates } from "@/hooks/useAgentStates";
import { inPipelineOrder } from "../pipelineOrder";
import type { LogEvent, PipelineGraph } from "@/lib/types";
import { loadFixture } from "./fixtures";

const t = "2026-10-19T00:00:00Z";

function cards(events: LogEvent[], status: string, graph: PipelineGraph) {
  return renderHook(() => useAgentStates(events, status, graph)).result.current.agents;
}
/** Each card as node and status, a run's attempt after its node. */
const named = (agents: ReturnType<typeof cards>) =>
  agents.map((a) => `${a.node}${a.attempt > 1 ? `#${a.attempt}` : ""}:${a.status}`);

describe("inPipelineOrder", () => {
  // One agent runs onboarding's analyze step; its other steps run without
  // one, so they are graph steps with no run of their own.
  const onboarding = loadFixture("onboarding");
  const event = onboarding.find((e) => e.type === "pipeline_graph");
  if (event?.type !== "pipeline_graph") throw new Error("the onboarding fixture has no pipeline_graph");
  const graph = event.graph;

  it("puts onboarding's steps back around its analyze run, in the graph's order", () => {
    const agents = cards(onboarding, "completed", graph);
    expect(named(agents), "the hook lists the run first").toEqual([
      "analyze:completed",
      "init_onboard:completed",
      "validate:completed",
      "smoke:completed",
      "write_config:completed",
      "cleanup:completed",
    ]);
    expect(named(inPipelineOrder(agents, graph))).toEqual([
      "init_onboard:completed",
      "analyze:completed",
      "validate:completed",
      "smoke:completed",
      "write_config:completed",
      "cleanup:completed",
    ]);
  });

  it("orders an onboarding job in flight the same way", () => {
    const upTo = onboarding.findIndex((e) => e.type === "agent_started") + 1;
    const agents = cards(onboarding.slice(0, upTo), "running", graph);
    expect(named(inPipelineOrder(agents, graph))).toEqual([
      "init_onboard:completed",
      "analyze:running",
      "validate:pending",
      "smoke:pending",
      "write_config:pending",
      "cleanup:pending",
    ]);
  });

  it("keeps a loop back from review to the developer as the job went, the steps ahead after it", () => {
    const loopGraph: PipelineGraph = {
      graph_id: "g",
      name: "g",
      nodes: [
        { node_id: "triage", agent_type: "triage", physical_node: "triage", position: { x: 0, y: 0 } },
        { node_id: "developer", agent_type: "developer", physical_node: "developer", position: { x: 1, y: 0 } },
        { node_id: "reviewer", agent_type: "reviewer", physical_node: "review", position: { x: 2, y: 0 } },
        { node_id: "qa", agent_type: "qa", physical_node: "qa", position: { x: 3, y: 0 } },
        { node_id: "output", agent_type: "output", physical_node: "finalize_output", position: { x: 4, y: 0 } },
      ],
      edges: [],
      metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "triage" },
    };
    const run = (node: string, runId: string, attempt: number, done: boolean) => [
      { type: "node_started", node, attempt, timestamp: t },
      { type: "agent_started", node, agent: node, run_id: runId, model: "m", max_turns: 10, timestamp: t },
      ...(done
        ? [
            { type: "agent_completed", node, agent: node, run_id: runId, is_error: false, output: "", error_message: "", cost_usd: 0, duration_ms: 1, timestamp: t },
            { type: "node_completed", node, attempt, timestamp: t },
          ]
        : []),
    ];
    const events = [
      ...run("triage", "r1", 1, true),
      ...run("developer", "r2", 1, true),
      ...run("review", "r3", 1, true),
      ...run("developer", "r4", 2, false),
    ] as LogEvent[];
    expect(named(inPipelineOrder(cards(events, "running", loopGraph), loopGraph))).toEqual([
      "triage:completed",
      "developer:completed",
      "review:completed",
      "developer#2:running",
      "qa:pending",
      "finalize_output:pending",
    ]);
  });

  it("leaves the cards as they are without a graph", () => {
    const agents = cards(onboarding, "completed", graph);
    expect(inPipelineOrder(agents, null)).toBe(agents);
  });
});
