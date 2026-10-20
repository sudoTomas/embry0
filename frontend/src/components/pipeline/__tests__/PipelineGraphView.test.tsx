import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import source from "../PipelineGraphView.tsx?raw";
import type { PipelineGraph } from "@/lib/types";
import { deriveNodeStates } from "@/lib/pipeline-live";
import { loadFixture } from "@/lib/__tests__/fixtures";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", display_name: "Developer", avatar: "smith", color: "amber", skills: [] }] }),
}));
import { PipelineGraphView, buildFlowData } from "../PipelineGraphView";

const graph: PipelineGraph = {
  graph_id: "g", name: "g", edges: [{ edge_id: "e", source: "s", target: "d", edge_type: "flow" }],
  nodes: [
    { node_id: "s", agent_type: "__start__", position: { x: 0, y: 0 } },
    { node_id: "d", agent_type: "developer", label: "developer", position: { x: 200, y: 0 } },
  ],
  metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "manual" },
};

describe("buildFlowData", () => {
  it("marks agent nodes live for PartyNode and keeps the sentinels on startEndNode", () => {
    const { nodes } = buildFlowData(graph, { d: { node_id: "d", agent_type: "developer", state: "running", iteration: 2, cost_usd: 0.2, turns: 3, duration_seconds: 4 } });
    const start = nodes.find((n) => n.id === "s")!;
    const dev = nodes.find((n) => n.id === "d")!;
    expect(start.type).toBe("startEndNode");
    expect(start.data).toEqual({ nodeRole: "start" });
    expect(dev.type).toBe("agentNode");
    expect(dev.data).toMatchObject({ agentType: "developer", mode: "live", state: "running", costUsd: 0.2, turns: 3, iteration: 2 });
    expect(dev.draggable).toBe(false);
  });

  it("gives a node no event touched the pending state", () => {
    const { nodes } = buildFlowData(graph, {});
    expect(nodes.find((n) => n.id === "d")!.data).toMatchObject({ state: "pending", costUsd: 0, turns: 0, iteration: 1 });
  });

  it("carries the states derived from the issue-to-pr fixture", () => {
    const events = loadFixture("issue-to-pr");
    const graphEvent = events.find((e) => e.type === "pipeline_graph");
    expect(graphEvent).toBeDefined();
    const fixtureGraph = (graphEvent as unknown as { graph: PipelineGraph }).graph;
    const { nodes } = buildFlowData(fixtureGraph, deriveNodeStates(fixtureGraph, events, "completed"));
    const states = Object.fromEntries(nodes.map((n) => [n.id, (n.data as { state: string }).state]));
    expect(Object.values(states)).toContain("completed");
    expect(Object.values(states)).not.toContain("ready");
    for (const n of nodes) expect(n.type).toBe("agentNode");
  });
});

describe("PipelineGraphView", () => {
  it("says it is waiting when there is no graph", () => {
    render(<PipelineGraphView graph={null} nodeStates={{}} />);
    expect(screen.getByText(/waiting for pipeline graph/i)).toBeInTheDocument();
  });

  it("keeps no renderer of its own and no hex colour", () => {
    expect(source).not.toContain("LiveAgentNode");
    expect(source).not.toMatch(/#[0-9a-fA-F]{6}\b/);
    expect(source).not.toContain("animate-pulse-glow");
  });
});
