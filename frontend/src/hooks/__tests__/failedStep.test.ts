// A failed pipeline step must read as failed. The rows below are the event
// stream a failed triage step left in job_logs (only the provider's wording
// is replaced): a pre-contract agent_started with no run_id, an
// agent_completed whose outcome sits in a nested `result`, a node-less error,
// and node_completed with action "failed". The Job page once showed this step
// as a green "Done".
import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";
import { useAgentStates } from "../useAgentStates";
import { deriveNodeStates } from "@/lib/pipeline-live";
import { normalizeWireEvent } from "@/lib/wire";
import type { PipelineGraph } from "@/lib/types";

const MSG = "the provider called the access token stale (HTTP 403)";

const rows = [
  { type: "node_started", node: "init", timestamp: "2026-10-17T22:28:03.734Z", event_seq: 1 },
  { type: "progress", message: "Sandbox container created", timestamp: "2026-10-17T22:28:05.843Z", event_seq: 2 },
  { type: "node_completed", node: "init", timestamp: "2026-10-17T22:28:06.243Z", event_seq: 3 },
  { type: "node_started", node: "triage", agent: "triage", timestamp: "2026-10-17T22:28:06.250Z", event_seq: 4 },
  { type: "agent_started", agent: "triage", timestamp: "2026-10-17T22:28:07.364Z", event_seq: 5 },
  { type: "error", error: MSG, timestamp: "2026-10-17T22:28:07.457Z", event_seq: 6 },
  {
    type: "cost_update", cost_usd: 0, duration_ms: 382, num_turns: 1, tokens_in: 0, tokens_out: 0,
    cache_read_tokens: 0, cache_creation_tokens: 0, node: "triage", timestamp: "2026-10-17T22:28:07.457Z", event_seq: 7,
  },
  {
    type: "agent_completed",
    result: { agent_type: "triage", is_error: true, error_message: MSG, output: "", cost_usd: 0, duration_ms: 382 },
    cost_usd: 0, duration_ms: 382, tools_called: {}, timestamp: "2026-10-17T22:28:07.457Z", event_seq: 8,
  },
  { type: "error", message: `Triage agent failed: ${MSG}`, timestamp: "2026-10-17T22:28:07.785Z", event_seq: 9 },
  { type: "node_completed", node: "triage", action: "failed", timestamp: "2026-10-17T22:28:07.790Z", event_seq: 10 },
];

const graph: PipelineGraph = {
  nodes: [
    { node_id: "triage", agent_type: "triage", label: "Triage" },
    { node_id: "developer", agent_type: "developer", label: "Developer" },
  ],
  edges: [{ source: "triage", target: "developer" }],
} as unknown as PipelineGraph;

const events = rows.map(normalizeWireEvent);

describe("a failed pipeline step", () => {
  it("the triage card is failed with the agent's error, not Done", () => {
    const { agents } = renderHook(() => useAgentStates(events, "failed", graph)).result.current;
    const triage = agents.find((a) => a.node === "triage");
    expect(triage?.status, "a step whose agent failed must not read completed").toBe("failed");
    expect(triage?.summary).toBe(MSG);
  });

  it("node_completed with action 'failed' fails a run that reported no error", () => {
    const contract = [
      { type: "node_started", node: "triage", attempt: 1, timestamp: "t" },
      { type: "agent_started", node: "triage", agent: "triage", run_id: "r1", model: "m", max_turns: 5, timestamp: "t" },
      { type: "node_completed", node: "triage", agent: "triage", attempt: 1, action: "failed", timestamp: "t" },
    ].map(normalizeWireEvent);
    const { agents } = renderHook(() => useAgentStates(contract, "running")).result.current;
    expect(agents[0].status, "the node's own failed verdict must win over a clean exit").toBe("failed");
  });

  it("node_completed with ok false fails the node in the pipeline graph", () => {
    const g = { nodes: [{ node_id: "validate", agent_type: "", label: "Validate" }], edges: [] } as unknown as PipelineGraph;
    const ev = [
      { type: "node_started", node: "validate", attempt: 1, timestamp: "t" },
      { type: "node_completed", node: "validate", attempt: 1, ok: false, timestamp: "t" },
    ].map(normalizeWireEvent);
    expect(deriveNodeStates(g, ev, "running").validate?.state).toBe("failed");
  });

  it("the pipeline graph shows the failed triage node as failed", () => {
    expect(deriveNodeStates(graph, events, "failed").triage?.state).toBe("failed");
  });
});
