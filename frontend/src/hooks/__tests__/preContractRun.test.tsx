// A job whose events predate the event contract must keep its Pipeline tab:
// one compact line per card, its duration and cost. The rows below have the
// shape such a job left in job_logs (the texts are replaced, the outputs
// padded to their stored length): agent_started and agent_completed carry
// neither run_id nor node, and agent_completed nests the run's outcome, the
// agent's whole final reply among it, in `result`. Lifting that reply onto
// the card once made each header thousands of characters long.
import { describe, expect, it, vi } from "vitest";
import { render, renderHook, screen } from "@testing-library/react";

// The card paints its agent from the identity source, which reads GET /agents.
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [], isLoading: false }) }));
import { useAgentStates } from "../useAgentStates";
import { AgentCard } from "@/components/jobs/AgentCard";
import { normalizeWireEvent } from "@/lib/wire";
import type { PipelineGraph } from "@/lib/types";

const reply = (lead: string, length: number) => `${lead} ${"x".repeat(length)}`.slice(0, length);
let seq = 100;
const at = (n: number) => `2026-10-13T16:4${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}.000000+00:00`;
const row = (n: number, fields: Record<string, unknown>) => ({ ...fields, timestamp: at(n), event_seq: seq++ });

/** One agent node as a pre-contract job stored it. */
function agentNode(node: string, start: number, cost: number, ms: number, output: string, action?: string) {
  return [
    row(start, { type: "node_started", node, agent: node }),
    row(start + 1, { type: "agent_started", agent: node }),
    row(start + 2, { type: "turn_start", turn_number: 1, model: "model-a", node }),
    row(start + 2, { type: "text", text: `Working on the ${node} step.`, node }),
    row(start + 3, { type: "tool_call", tool_name: "Grep", tool_id: `call-${node}-0`, input: "/workspace", tool_input: { pattern: "x" }, node }),
    row(start + 3, { type: "tool_result", tool_use_id: `call-${node}-0`, content: "match", is_error: false, node }),
    row(start + 4, {
      type: "cost_update", cost_usd: cost, duration_ms: ms, num_turns: 2, tokens_in: 9842, tokens_out: 1506,
      cache_read_tokens: 5632, cache_creation_tokens: 0, node,
    }),
    row(start + 4, {
      type: "agent_completed",
      result: {
        agent_type: node, is_error: false, error_message: "", output, cost_usd: cost, duration_ms: ms,
        tools_called: { Grep: 1 }, input_tokens: 9842, output_tokens: 1506, cache_read_tokens: 5632, cache_creation_tokens: 0,
      },
      cost_usd: cost, duration_ms: ms, tools_called: { Grep: 1 },
    }),
    row(start + 5, { type: "node_completed", node, ...(action ? { action } : {}) }),
  ];
}

const rows = [
  row(0, { type: "node_started", node: "init" }),
  row(1, { type: "progress", message: "Sandbox container created" }),
  row(2, { type: "node_completed", node: "init" }),
  ...agentNode("triage", 3, 0.02872, 19483, reply("I'll confirm the docs line first.", 1017), "proceed"),
  row(9, { type: "node_started", node: "plan_route" }),
  row(9, { type: "node_completed", node: "plan_route", job_kind: "code", template: "quick-fix" }),
  ...agentNode("developer", 10, 0.036814, 31422, reply("I'll update the docs line.", 3000)),
  ...agentNode("review", 16, 0.17297, 115466, reply("I'll review the change against the issue.", 2361)),
  row(22, { type: "node_started", node: "verify" }),
  row(22, { type: "node_completed", node: "verify", action: "shadow" }),
];

const graph = {
  nodes: [
    { node_id: "triage", agent_type: "triage", physical_node: "triage", label: "triage" },
    { node_id: "developer", agent_type: "developer", physical_node: "developer", label: "developer" },
    { node_id: "reviewer", agent_type: "reviewer", physical_node: "review", label: "reviewer" },
  ],
  edges: [
    { source: "triage", target: "developer", edge_type: "flow" },
    { source: "developer", target: "reviewer", edge_type: "flow" },
  ],
} as unknown as PipelineGraph;

const events = rows.map(normalizeWireEvent);

function cards() {
  return renderHook(() => useAgentStates(events, "pr_created", graph)).result.current.agents;
}

describe("a job from before the event contract, on the Pipeline tab", () => {
  it("keeps one card per agent run, each done, with the run's own cost and time", () => {
    expect(
      cards().map((a) => [a.node, a.status, a.costUsd, a.durationMs]),
    ).toEqual([
      ["triage", "completed", 0.02872, 19483],
      ["developer", "completed", 0.036814, 31422],
      ["review", "completed", 0.17297, 115466],
    ]);
  });

  it("keeps the agent's final reply off the card: the summary is the node's verdict or nothing", () => {
    expect(cards().map((a) => [a.node, a.summary])).toEqual([
      ["triage", "proceed"],
      ["developer", ""],
      ["review", ""],
    ]);
  });

  it("draws each collapsed card as one short line", () => {
    const developer = cards().find((a) => a.node === "developer")!;
    const { container } = render(<AgentCard agent={developer} />);
    expect(screen.getByText("Developer"), "the identity's display name").toBeInTheDocument();
    expect(container.textContent, "the collapsed card must not carry the agent's reply").toBe("✓DeveloperDone31s · $0.04");
  });
});
