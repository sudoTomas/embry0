import { describe, it, expect } from "vitest";
import { buildBlockedByGraph } from "../blockedByGraph";
import type { AgentTaskBlockedBy } from "@/api/agent";

describe("buildBlockedByGraph", () => {
  it("returns just the root node when nothing is blocking", () => {
    const data: AgentTaskBlockedBy = { id: "root", blocked_by: [] };
    const { nodes, edges } = buildBlockedByGraph(data, "root-label");
    expect(nodes).toHaveLength(1);
    expect(nodes[0].id).toBe("root");
    expect(edges).toHaveLength(0);
  });

  it("renders one blocker child + one edge from blocker → root", () => {
    const data: AgentTaskBlockedBy = {
      id: "root",
      blocked_by: [{ id: "b1", status: "running", title: "Build base" }],
    };
    const { nodes, edges } = buildBlockedByGraph(data, "Root task");

    expect(nodes.map((n) => n.id).sort()).toEqual(["b1", "root"]);
    expect(edges).toHaveLength(1);
    expect(edges[0]).toMatchObject({ source: "b1", target: "root" });
  });

  it("dagre lays out nodes at distinct positions", () => {
    const data: AgentTaskBlockedBy = {
      id: "root",
      blocked_by: [
        { id: "b1", status: "running" },
        { id: "b2", status: "queued" },
      ],
    };
    const { nodes } = buildBlockedByGraph(data, "Root");
    const positions = nodes.map((n) => `${n.position.x},${n.position.y}`);
    const unique = new Set(positions);
    expect(unique.size).toBe(nodes.length);
  });

  it("blocker nodes carry { label, status } in data for BlockedNode", () => {
    const data: AgentTaskBlockedBy = {
      id: "root",
      blocked_by: [{ id: "b1", status: "failed", title: "Boom" }],
    };
    const { nodes } = buildBlockedByGraph(data, "Root");
    const b1 = nodes.find((n) => n.id === "b1");
    expect(b1?.data).toMatchObject({ label: "Boom", status: "failed" });
    const root = nodes.find((n) => n.id === "root");
    expect(root?.data).toMatchObject({ label: "Root", status: "selected" });
  });
});
