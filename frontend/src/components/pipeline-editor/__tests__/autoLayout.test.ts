import { describe, expect, it } from "vitest";
import type { Edge, Node } from "@xyflow/react";
import { autoLayout } from "../autoLayout";

const node = (id: string, agentType = "developer"): Node => ({
  id,
  position: { x: 0, y: 0 },
  data: { agentType, label: id },
  type: "agentNode",
});

describe("autoLayout — empty / single", () => {
  it("returns an empty result when given no nodes", () => {
    const result = autoLayout([], []);
    expect(result.nodes).toEqual([]);
    expect(result.edges).toEqual([]);
  });

  it("preserves a single node with a numeric position", () => {
    const result = autoLayout([node("a")], []);
    expect(result.nodes).toHaveLength(1);
    expect(result.nodes[0].id).toBe("a");
    expect(typeof result.nodes[0].position.x).toBe("number");
    expect(typeof result.nodes[0].position.y).toBe("number");
  });
});

describe("autoLayout — LR fallback (dagre)", () => {
  it("lays out a→b left-to-right when no canonical cycle is present", () => {
    const nodes = [node("a", "explorer"), node("b", "explorer")];
    const edges: Edge[] = [{ id: "e1", source: "a", target: "b" }];
    const result = autoLayout(nodes, edges, "LR");
    const a = result.nodes.find((n) => n.id === "a")!;
    const b = result.nodes.find((n) => n.id === "b")!;
    expect(b.position.x).toBeGreaterThan(a.position.x);
  });

  it("ignores feedback edges in dagre layout", () => {
    const nodes = [node("a", "explorer"), node("b", "explorer"), node("c", "output")];
    const edges: Edge[] = [
      { id: "e1", source: "a", target: "b" },
      { id: "e2", source: "b", target: "c" },
      { id: "e3", source: "c", target: "a", type: "feedbackEdge" },
    ];
    const result = autoLayout(nodes, edges, "LR");
    const xs = Object.fromEntries(result.nodes.map((n) => [n.id, n.position.x]));
    expect(xs.b).toBeGreaterThan(xs.a);
    expect(xs.c).toBeGreaterThan(xs.b);
  });

  it("returns existing edges unchanged when falling through to dagre", () => {
    const nodes = [node("a", "explorer"), node("b", "explorer")];
    const edges: Edge[] = [{ id: "e1", source: "a", target: "b" }];
    const result = autoLayout(nodes, edges, "LR");
    expect(result.edges).toEqual(edges);
  });
});

describe("autoLayout — circular dispatch", () => {
  it("dispatches to circular layout when ≥2 cardinal stages are present", () => {
    const nodes = [node("t", "triage"), node("d", "developer")];
    const result = autoLayout(nodes, []);
    const t = result.nodes.find((n) => n.id === "t")!;
    const d = result.nodes.find((n) => n.id === "d")!;
    expect(t.position.y).toBeLessThan(0);
    expect(d.position.x).toBeGreaterThan(0);
  });

  it("infers canonical edges when dispatching circular", () => {
    const nodes = [node("t", "triage"), node("d", "developer")];
    const result = autoLayout(nodes, []);
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0].source).toBe("t");
    expect(result.edges[0].target).toBe("d");
    expect(result.edges[0].data?.inferred).toBe(true);
  });

  it("falls through to LR when only one cardinal stage type is present", () => {
    const nodes = [node("d1", "developer"), node("d2", "developer")];
    const edges: Edge[] = [{ id: "e1", source: "d1", target: "d2" }];
    const result = autoLayout(nodes, edges, "LR");
    const d1 = result.nodes.find((n) => n.id === "d1")!;
    const d2 = result.nodes.find((n) => n.id === "d2")!;
    expect(d2.position.x).toBeGreaterThan(d1.position.x);
    expect(result.edges).toEqual(edges);
  });
});

describe("autoLayout — purity", () => {
  it("does not mutate the input nodes", () => {
    const original = node("a", "explorer");
    original.position = { x: 100, y: 200 };
    const result = autoLayout([original], []);
    expect(original.position).toEqual({ x: 100, y: 200 });
    expect(result.nodes[0]).not.toBe(original);
  });
});
