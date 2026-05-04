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
    // Two nodes with a real edge + a feedback edge between them.
    // Should fall through to LR (no canonical cycle, real edges exist,
    // nodes < 3) and the feedback edge must not pull positions back.
    const nodes = [node("a", "explorer"), node("b", "explorer")];
    const edges: Edge[] = [
      { id: "e1", source: "a", target: "b" },
      { id: "e2", source: "b", target: "a", type: "feedbackEdge" },
    ];
    const result = autoLayout(nodes, edges, "LR");
    const xs = Object.fromEntries(result.nodes.map((n) => [n.id, n.position.x]));
    expect(xs.b).toBeGreaterThan(xs.a);
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

  it("falls through to LR when only one cardinal stage type is present AND a real edge exists", () => {
    // 2 nodes + a real edge = clean DAG; LR is meaningful → use LR.
    const nodes = [node("d1", "developer"), node("d2", "developer")];
    const edges: Edge[] = [{ id: "e1", source: "d1", target: "d2" }];
    const result = autoLayout(nodes, edges, "LR");
    const d1 = result.nodes.find((n) => n.id === "d1")!;
    const d2 = result.nodes.find((n) => n.id === "d2")!;
    expect(d2.position.x).toBeGreaterThan(d1.position.x);
    expect(result.edges).toEqual(edges);
  });

  it("uses circular when the same-stage nodes are unconnected (would degenerate in dagre)", () => {
    // Reported bug: 2 developers + START + END with no edges produced a vertical
    // column. Circular + auto-connect chain is the intended behavior.
    const nodes = [
      node("d1", "developer"),
      node("d2", "developer"),
      { id: "start", position: { x: 0, y: 0 }, data: { label: "Start" }, type: "start" },
      { id: "end", position: { x: 0, y: 0 }, data: { label: "End" }, type: "end" },
    ];
    const result = autoLayout(nodes, []);
    // 3 inferred chain edges should be created
    expect(result.edges).toHaveLength(3);
    expect(result.edges.every((e) => e.data?.inferred === true)).toBe(true);
    // Nodes spread on a circle (not all in a vertical column)
    const xs = result.nodes.map((n) => n.position.x);
    const ys = result.nodes.map((n) => n.position.y);
    expect(Math.max(...xs) - Math.min(...xs)).toBeGreaterThan(100);
    expect(Math.max(...ys) - Math.min(...ys)).toBeGreaterThan(100);
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
