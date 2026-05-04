import { describe, expect, it } from "vitest";
import type { Edge, Node } from "@xyflow/react";
import { circularLayout, canonicalCycleDetected, shouldUseCircular, CIRCLE_RADIUS } from "../circularLayout";

const node = (id: string, agentType: string): Node => ({
  id,
  position: { x: 0, y: 0 },
  data: { agentType, label: id },
  type: "agentNode",
});

describe("canonicalCycleDetected", () => {
  it("returns false for empty graph", () => {
    expect(canonicalCycleDetected([])).toBe(false);
  });

  it("returns false for non-cardinal nodes only", () => {
    expect(canonicalCycleDetected([node("e1", "explorer"), node("o1", "output")])).toBe(false);
  });

  it("returns false when only one cardinal stage is represented", () => {
    expect(canonicalCycleDetected([node("d1", "developer"), node("d2", "developer")])).toBe(false);
  });

  it("returns true when two distinct cardinal stages are represented", () => {
    expect(canonicalCycleDetected([node("t", "triage"), node("d", "developer")])).toBe(true);
  });

  it("returns true for the full canonical four", () => {
    const nodes = [
      node("t", "triage"),
      node("d", "developer"),
      node("r", "reviewer"),
      node("q", "qa"),
    ];
    expect(canonicalCycleDetected(nodes)).toBe(true);
  });
});

describe("circularLayout — placement", () => {
  it("returns inputs unchanged when no cardinal stage nodes are present", () => {
    const nodes = [node("e", "explorer")];
    const result = circularLayout(nodes, []);
    expect(result.nodes).toHaveLength(1);
    expect(result.edges).toEqual([]);
  });

  it("places triage at the north cardinal point", () => {
    const result = circularLayout([node("t", "triage")], []);
    const t = result.nodes.find((n) => n.id === "t")!;
    // North = (0, -R) relative to circle center; in absolute coords the center
    // can be any positive offset, but t should sit ABOVE the center.
    expect(t.position.y).toBeLessThan(0);
    expect(Math.abs(t.position.x)).toBeLessThan(1); // approximately on the vertical axis
  });

  it("places the four cardinal stages at NESW positions in canonical order", () => {
    const nodes = [
      node("t", "triage"),
      node("d", "developer"),
      node("r", "reviewer"),
      node("q", "qa"),
    ];
    const result = circularLayout(nodes, []);
    const positions = Object.fromEntries(result.nodes.map((n) => [n.id, n.position]));
    // triage = N (smallest y, i.e. most negative or zero)
    expect(positions.t.y).toBeLessThan(positions.d.y);
    expect(positions.t.y).toBeLessThan(positions.r.y);
    // develop = E (largest x)
    expect(positions.d.x).toBeGreaterThan(positions.t.x);
    expect(positions.d.x).toBeGreaterThan(positions.r.x);
    expect(positions.d.x).toBeGreaterThan(positions.q.x);
    // validate (review) = S (largest y)
    expect(positions.r.y).toBeGreaterThan(positions.t.y);
    expect(positions.r.y).toBeGreaterThan(positions.d.y);
    expect(positions.r.y).toBeGreaterThan(positions.q.y);
    // qa = W (smallest x)
    expect(positions.q.x).toBeLessThan(positions.t.x);
    expect(positions.q.x).toBeLessThan(positions.d.x);
    expect(positions.q.x).toBeLessThan(positions.r.x);
  });

  it("places non-cardinal nodes on an outer ring at radius 1.5R", () => {
    const nodes = [node("t", "triage"), node("d", "developer"), node("e", "explorer")];
    const result = circularLayout(nodes, []);
    const e = result.nodes.find((n) => n.id === "e")!;
    const distFromCenter = Math.sqrt(e.position.x ** 2 + e.position.y ** 2);
    expect(distFromCenter).toBeGreaterThan(CIRCLE_RADIUS * 1.3);
    expect(distFromCenter).toBeLessThan(CIRCLE_RADIUS * 1.7);
  });

  it("clusters multiple nodes at the same cardinal stage with angular offsets", () => {
    const nodes = [
      node("t", "triage"),
      node("d1", "developer"),
      node("d2", "developer"),
    ];
    const result = circularLayout(nodes, []);
    const d1 = result.nodes.find((n) => n.id === "d1")!;
    const d2 = result.nodes.find((n) => n.id === "d2")!;
    // Both near east cardinal but not at exactly the same position
    expect(d1.position).not.toEqual(d2.position);
    // Both still roughly in the east hemisphere (positive x)
    expect(d1.position.x).toBeGreaterThan(0);
    expect(d2.position.x).toBeGreaterThan(0);
  });

  it("places Start and End sentinels on the equator outside the circle", () => {
    const nodes = [
      node("t", "triage"),
      node("d", "developer"),
      { id: "start", position: { x: 0, y: 0 }, data: { label: "Start" }, type: "start" },
      { id: "end", position: { x: 0, y: 0 }, data: { label: "End" }, type: "end" },
    ];
    const result = circularLayout(nodes, []);
    const start = result.nodes.find((n) => n.id === "start")!;
    const end = result.nodes.find((n) => n.id === "end")!;
    expect(start.position.x).toBeLessThan(-CIRCLE_RADIUS);
    expect(end.position.x).toBeGreaterThan(CIRCLE_RADIUS);
    expect(Math.abs(start.position.y)).toBeLessThan(10);
    expect(Math.abs(end.position.y)).toBeLessThan(10);
  });

  it("returns existing edges plus inferred edges for the canonical cycle", () => {
    const nodes = [
      node("t", "triage"),
      node("d", "developer"),
      node("r", "reviewer"),
      node("q", "qa"),
    ];
    const result = circularLayout(nodes, []);
    expect(result.edges).toHaveLength(4);
    const inferred = result.edges.filter((e) => e.data?.inferred);
    expect(inferred).toHaveLength(4);
  });

  it("does not mutate input nodes", () => {
    const original = node("t", "triage");
    original.position = { x: 999, y: 999 };
    const result = circularLayout([original], []);
    expect(original.position).toEqual({ x: 999, y: 999 });
    expect(result.nodes[0]).not.toBe(original);
  });
});

describe("shouldUseCircular", () => {
  it("returns false for empty graph or single node", () => {
    expect(shouldUseCircular([], [])).toBe(false);
    expect(shouldUseCircular([node("a", "developer")], [])).toBe(false);
  });

  it("returns true when canonical cycle is detected", () => {
    const nodes = [node("t", "triage"), node("d", "developer")];
    expect(shouldUseCircular(nodes, [])).toBe(true);
  });

  it("returns true for ≥2 nodes with no real edges (dagre would degenerate)", () => {
    const nodes = [node("d1", "developer"), node("d2", "developer")];
    expect(shouldUseCircular(nodes, [])).toBe(true);
  });

  it("ignores feedback edges when deciding", () => {
    const nodes = [node("d1", "developer"), node("d2", "developer")];
    const feedback: Edge[] = [
      { id: "fb", source: "d1", target: "d2", type: "feedbackEdge" },
    ];
    // No real edges → still circular
    expect(shouldUseCircular(nodes, feedback)).toBe(true);
  });

  it("returns true for ≥3 nodes even when sparse edges exist (prefer circular over LR)", () => {
    const nodes = [
      node("a", "developer"),
      node("b", "developer"),
      node("c", "developer"),
    ];
    const edges: Edge[] = [{ id: "e", source: "a", target: "b" }];
    expect(shouldUseCircular(nodes, edges)).toBe(true);
  });

  it("returns false for 2 nodes with a real edge between them (clean LR works)", () => {
    const nodes = [node("a", "developer"), node("b", "developer")];
    const edges: Edge[] = [{ id: "e", source: "a", target: "b" }];
    expect(shouldUseCircular(nodes, edges)).toBe(false);
  });
});

describe("circularLayout — uniform fallback (no canonical cycle)", () => {
  const startNode = (id: string): Node => ({
    id,
    position: { x: 0, y: 0 },
    data: { label: id },
    type: "start",
  });
  const endNode = (id: string): Node => ({
    id,
    position: { x: 0, y: 0 },
    data: { label: id },
    type: "end",
  });

  it("distributes 4 unconnected nodes evenly around the circle starting at top", () => {
    const nodes = [
      node("d1", "developer"),
      node("d2", "developer"),
      startNode("start"),
      endNode("end"),
    ];
    const result = circularLayout(nodes, []);
    expect(result.nodes).toHaveLength(4);

    // Sort order: start, dev1, dev2 (alphabetical by id), end
    // Placed at: N (-90°), E (0°), S (90°), W (180°)
    const positions = Object.fromEntries(result.nodes.map((n) => [n.id, n.position]));
    expect(positions.start.y).toBeLessThan(0); // N
    expect(positions.start.x).toBeCloseTo(0, 0);
    expect(positions.d1.x).toBeGreaterThan(0); // E
    expect(positions.d1.y).toBeCloseTo(0, 0);
    expect(positions.d2.y).toBeGreaterThan(0); // S
    expect(positions.end.x).toBeLessThan(0); // W
  });

  it("auto-connects 4 unconnected nodes as a chain (start → mid → mid → end)", () => {
    const nodes = [
      node("d1", "developer"),
      node("d2", "developer"),
      startNode("start"),
      endNode("end"),
    ];
    const result = circularLayout(nodes, []);
    // 3 inferred edges in chain order: start→d1, d1→d2, d2→end
    expect(result.edges).toHaveLength(3);
    const pairs = result.edges.map((e) => `${e.source}->${e.target}`);
    expect(pairs).toEqual(["start->d1", "d1->d2", "d2->end"]);
    for (const edge of result.edges) {
      expect(edge.data?.inferred).toBe(true);
    }
  });

  it("does not duplicate edges when chain already exists", () => {
    const nodes = [
      node("d1", "developer"),
      node("d2", "developer"),
    ];
    const existing: Edge[] = [{ id: "e1", source: "d1", target: "d2" }];
    const result = circularLayout(nodes, existing);
    expect(result.edges).toHaveLength(1);
    expect(result.edges[0]).toBe(existing[0]);
  });

  it("preserves existing edges and adds only the missing chain links", () => {
    const nodes = [
      node("d1", "developer"),
      node("d2", "developer"),
      node("d3", "developer"),
    ];
    const existing: Edge[] = [{ id: "e1", source: "d1", target: "d2" }];
    const result = circularLayout(nodes, existing);
    // Existing d1→d2 preserved; d2→d3 inferred
    expect(result.edges).toHaveLength(2);
    expect(result.edges.find((e) => e.source === "d1" && e.target === "d2")).toBe(existing[0]);
    const newEdge = result.edges.find((e) => e.id?.startsWith("inferred-chain-"));
    expect(newEdge?.source).toBe("d2");
    expect(newEdge?.target).toBe("d3");
  });
});
