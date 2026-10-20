import { describe, expect, it } from "vitest";
import type { PipelineGraph } from "@/lib/types";
import { firstPartyMember } from "../firstPartyMember";

const graph = (nodes: PipelineGraph["nodes"], edges: PipelineGraph["edges"] = []): PipelineGraph => ({
  graph_id: "g", name: "g", nodes, edges, metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "manual" },
});
const node = (id: string, type: string, x: number): PipelineGraph["nodes"][number] => ({ node_id: id, agent_type: type, position: { x, y: 0 } });

describe("firstPartyMember", () => {
  it("is the target of the edge out of the start sentinel", () => {
    const g = graph([node("s", "__start__", 0), node("r", "research", 300), node("d", "developer", 100)], [
      { edge_id: "e1", source: "s", target: "r", edge_type: "flow" },
    ]);
    expect(firstPartyMember(g)).toBe("research");
  });
  it("is the leftmost agent node when no sentinel edge exists", () => {
    expect(firstPartyMember(graph([node("r", "research", 300), node("d", "developer", 100)]))).toBe("developer");
  });
  it("is triage when there is no graph or no agent node", () => {
    expect(firstPartyMember(undefined)).toBe("triage");
    expect(firstPartyMember(graph([node("s", "__start__", 0), node("e", "__end__", 10)]))).toBe("triage");
  });
});
