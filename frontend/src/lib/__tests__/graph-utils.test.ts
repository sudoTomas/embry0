import { describe, it, expect, vi } from "vitest";
import { toApiGraph, deserializeGraph, injectStartEndNodes } from "@/lib/graph-utils";
import type { PipelineGraph, AgentNode } from "@/lib/types";

/**
 * RAV-1293 — the canvas keeps START/END for readability, the API rejects them.
 * Every save from the editor 422'd on "unknown agent_type '__start__'" because
 * the scaffolding was sent verbatim.
 */

function node(node_id: string, agent_type: string, x = 0): AgentNode {
  return {
    node_id,
    agent_type,
    label: agent_type,
    position: { x, y: 0 },
    model: null,
    max_budget_usd: null,
    max_turns: null,
    effort: null,
    tools: null,
    skills: null,
    prompt_prepend: null,
    prompt_append: null,
    custom_prompt: null,
    custom_tools: null,
    sandbox: null,
  } as AgentNode;
}

function canvasGraph(): PipelineGraph {
  return {
    graph_id: "graph-1",
    name: "quick-fix",
    nodes: [
      node("__start__", "__start__"),
      node("triage", "triage", 100),
      node("developer", "developer", 240),
      node("__end__", "__end__", 400),
    ],
    edges: [
      { edge_id: "e0", source: "__start__", target: "triage", edge_type: "flow", loop_config: null },
      { edge_id: "e1", source: "triage", target: "developer", edge_type: "flow", loop_config: null },
      { edge_id: "e2", source: "developer", target: "__end__", edge_type: "flow", loop_config: null },
    ],
    metadata: {},
  } as PipelineGraph;
}

describe("toApiGraph", () => {
  it("drops the START/END nodes", () => {
    const out = toApiGraph(canvasGraph());
    expect(out.nodes.map((n) => n.agent_type)).toEqual(["triage", "developer"]);
  });

  it("drops every edge touching the scaffolding, keeping the agent chain", () => {
    const out = toApiGraph(canvasGraph());
    expect(out.edges).toHaveLength(1);
    expect(out.edges[0]).toMatchObject({ source: "triage", target: "developer" });
  });

  it("leaves a graph that has no scaffolding untouched", () => {
    const stored: PipelineGraph = {
      ...canvasGraph(),
      nodes: [node("triage", "triage"), node("developer", "developer", 240)],
      edges: [{ edge_id: "e1", source: "triage", target: "developer", edge_type: "flow", loop_config: null }],
    };
    expect(toApiGraph(stored)).toBe(stored);
  });

  it("preserves name and metadata so the saved template keeps its identity", () => {
    const out = toApiGraph(canvasGraph());
    expect(out.name).toBe("quick-fix");
    expect(out.graph_id).toBe("graph-1");
  });

  it("leaves exactly one root node, which is what the backend validator requires", () => {
    const out = toApiGraph(canvasGraph());
    const targets = new Set(out.edges.map((e) => e.target));
    const roots = out.nodes.filter((n) => !targets.has(n.node_id));
    expect(roots.map((n) => n.node_id)).toEqual(["triage"]);
  });

  it("round-trips: the editor's load path restores the scaffolding it stripped", () => {
    // useGraphState loads with injectStartEndNodes(deserializeGraph(graph)).
    const stripped = toApiGraph(canvasGraph());
    const { nodes } = injectStartEndNodes(deserializeGraph(stripped));
    const roles = nodes.filter((n) => n.type === "startEndNode").map((n) => (n.data as { nodeRole: string }).nodeRole);
    expect(roles.sort()).toEqual(["end", "start"]);
    expect(nodes.filter((n) => n.type === "agentNode").map((n) => n.id)).toEqual(["triage", "developer"]);
  });

  it("connects the injected Start and End to the roots and leaves, past feedback edges", () => {
    const stored: PipelineGraph = {
      ...canvasGraph(),
      nodes: [node("triage", "triage"), node("developer", "developer", 240), node("review", "review", 400)],
      edges: [
        { edge_id: "e1", source: "triage", target: "developer", edge_type: "flow", loop_config: null },
        { edge_id: "e2", source: "developer", target: "review", edge_type: "flow", loop_config: null },
        { edge_id: "fb", source: "review", target: "developer", edge_type: "feedback", loop_config: null },
      ],
    };
    const { edges } = injectStartEndNodes(deserializeGraph(stored));
    const scaffold = edges.filter((e) => e.source === "__start__" || e.target === "__end__");
    expect(scaffold.map((e) => `${e.source}->${e.target}`)).toEqual(["__start__->triage", "review->__end__"]);
  });
});

import fixtureText from "../../../../tests/fixtures/templates/editor-config-shape.json?raw";
import { serializeGraph } from "@/lib/graph-utils";
import type { Node, Edge } from "@xyflow/react";

describe("serializeGraph writes overrides under config (#78, shared fixture)", () => {
  const fixture = JSON.parse(fixtureText) as PipelineGraph;
  const rf = deserializeGraph(fixture);
  it("round-trips the fixture byte for byte", () => {
    const out = serializeGraph(rf.nodes, rf.edges, fixture.metadata, fixture.name, fixture.graph_id);
    expect(JSON.stringify(out, null, 2) + "\n").toBe(fixtureText);
  });
  it("writes nothing at the node's top level but node_id, agent_type, label, position, config", () => {
    const out = serializeGraph(rf.nodes, rf.edges, fixture.metadata, fixture.name, fixture.graph_id);
    for (const n of out.nodes) expect(Object.keys(n).sort()).toEqual(["agent_type", "config", "label", "node_id", "position"]);
  });
  it("reads a legacy node (top-level overrides, bare skills list) as config with replace", () => {
    const legacy = { ...fixture, nodes: [{ node_id: "d", agent_type: "developer", position: { x: 0, y: 0 }, model: "m", skills: ["a:x"] }] } as PipelineGraph;
    const data = deserializeGraph(legacy).nodes[0].data as Record<string, unknown>;
    expect(data.model).toBe("m");
    expect(data.skills).toEqual({ add: ["a:x"], remove: "*" });
    const out = serializeGraph(deserializeGraph(legacy).nodes, [], fixture.metadata, "x", "g");
    expect(out.nodes[0].config).toEqual({ model: "m", skills: { add: ["a:x"], remove: "*" } });
  });
  it("prefers config over legacy keys when both exist", () => {
    const both = { ...fixture, nodes: [{ node_id: "d", agent_type: "developer", position: { x: 0, y: 0 }, model: "old", config: { model: "new" } }] } as PipelineGraph;
    expect((deserializeGraph(both).nodes[0].data as Record<string, unknown>).model).toBe("new");
  });
  it("drops an empty delta", () => {
    const n: Node = { id: "d", type: "agentNode", position: { x: 0, y: 0 }, data: { agentType: "developer", label: "Developer", skills: { add: [], remove: [] } } };
    const out = serializeGraph([n], [] as Edge[], fixture.metadata, "x", "g");
    expect(out.nodes[0].config).toEqual({});
  });
});

vi.mock("sonner", () => ({ toast: { warning: vi.fn(), error: vi.fn(), success: vi.fn() } }));
import { toast } from "sonner";

describe("an unreadable skills override", () => {
  it("is dropped and the drop is announced", () => {
    vi.mocked(toast.warning).mockClear();
    const bad = { ...JSON.parse(fixtureText), nodes: [{ node_id: "d", agent_type: "developer", position: { x: 0, y: 0 }, config: { skills: { add: "nope" } } }] } as PipelineGraph;
    const data = deserializeGraph(bad).nodes[0].data as Record<string, unknown>;
    expect(data.skills).toBeNull();
    expect(toast.warning).toHaveBeenCalledWith("Node d: its skills override could not be read and was dropped");
  });
});
