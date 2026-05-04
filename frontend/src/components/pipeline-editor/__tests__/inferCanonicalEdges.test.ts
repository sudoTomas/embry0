import { describe, expect, it } from "vitest";
import type { Edge, Node } from "@xyflow/react";
import { inferCanonicalEdges } from "../inferCanonicalEdges";

const node = (id: string, agentType: string): Node => ({
  id,
  position: { x: 0, y: 0 },
  data: { agentType, label: id },
  type: "agentNode",
});

describe("inferCanonicalEdges", () => {
  it("returns no edges for an empty graph", () => {
    expect(inferCanonicalEdges([], [])).toEqual([]);
  });

  it("returns no edges when only one cardinal stage is represented", () => {
    const nodes = [node("d1", "developer"), node("d2", "developer")];
    expect(inferCanonicalEdges(nodes, [])).toEqual([]);
  });

  it("creates the four canonical edges when all four stages are present and unconnected", () => {
    const nodes = [
      node("t", "triage"),
      node("d", "developer"),
      node("r", "reviewer"),
      node("q", "qa"),
    ];
    const result = inferCanonicalEdges(nodes, []);
    expect(result).toHaveLength(4);
    const pairs = result.map((e) => `${e.source}->${e.target}`).sort();
    expect(pairs).toEqual(["d->r", "q->t", "r->q", "t->d"]);
    for (const edge of result) {
      expect(edge.data?.inferred).toBe(true);
    }
  });

  it("creates only the missing edges when some canonical edges already exist", () => {
    const nodes = [
      node("t", "triage"),
      node("d", "developer"),
      node("r", "reviewer"),
      node("q", "qa"),
    ];
    const existing: Edge[] = [
      { id: "e1", source: "t", target: "d" },
      { id: "e2", source: "d", target: "r" },
    ];
    const result = inferCanonicalEdges(nodes, existing);
    expect(result).toHaveLength(2);
    const pairs = result.map((e) => `${e.source}->${e.target}`).sort();
    expect(pairs).toEqual(["q->t", "r->q"]);
  });

  it("creates no edges when all four canonical transitions are already wired", () => {
    const nodes = [
      node("t", "triage"),
      node("d", "developer"),
      node("r", "reviewer"),
      node("q", "qa"),
    ];
    const existing: Edge[] = [
      { id: "e1", source: "t", target: "d" },
      { id: "e2", source: "d", target: "r" },
      { id: "e3", source: "r", target: "q" },
      { id: "e4", source: "q", target: "t" },
    ];
    expect(inferCanonicalEdges(nodes, existing)).toEqual([]);
  });

  it("creates a partial-cycle edge when only two adjacent stages are present", () => {
    const nodes = [node("t", "triage"), node("d", "developer")];
    const result = inferCanonicalEdges(nodes, []);
    expect(result).toHaveLength(1);
    expect(result[0].source).toBe("t");
    expect(result[0].target).toBe("d");
  });

  it("creates only one edge per stage transition even with multiple instances per stage", () => {
    const nodes = [
      node("t", "triage"),
      node("d1", "developer"),
      node("d2", "developer"),
      node("r", "reviewer"),
    ];
    const result = inferCanonicalEdges(nodes, []);
    // T→D (one edge to either d1 or d2), D→R (one edge from either d1 or d2 to r)
    expect(result).toHaveLength(2);
    expect(result.find((e) => e.source === "t" && e.target.startsWith("d"))).toBeDefined();
    expect(result.find((e) => e.source.startsWith("d") && e.target === "r")).toBeDefined();
  });

  it("respects existing edges between any X-stage and Y-stage instance, not just the chosen primary", () => {
    const nodes = [
      node("t", "triage"),
      node("d1", "developer"),
      node("d2", "developer"),
    ];
    // Edge from triage to d2 already exists — inference should NOT add t→d1 too.
    const existing: Edge[] = [{ id: "e1", source: "t", target: "d2" }];
    expect(inferCanonicalEdges(nodes, existing)).toEqual([]);
  });
});
