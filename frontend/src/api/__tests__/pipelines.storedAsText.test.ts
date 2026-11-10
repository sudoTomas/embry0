// A template whose graph is too deeply nested for the API to send as an object arrives with the graph's JSON
// text (#431): the template carries it as graph_text, with no graph to open in the editor.
import { describe, it, expect, vi } from "vitest";

const get = vi.hoisted(() => vi.fn());
vi.mock("../client", () => ({ api: { get } }));

import { fetchTemplate } from "../pipelines";

describe("fetchTemplate — a graph stored as text (#431)", () => {
  it("keeps the text apart from the graph", async () => {
    const text = '{"nodes": [], "notes": {"a": {"a": "leaf"}}}';
    get.mockResolvedValue({ data: { id: "tpl-deep", name: "Deep one", description: "", graph_definition: text } });
    const template = await fetchTemplate("tpl-deep");
    expect(template.graph, "no graph the editor would read as an object").toBeNull();
    expect(template.graph_text).toBe(text);
  });

  it("leaves an object graph as it is", async () => {
    const graph = { graph_id: "g", name: "g", nodes: [], edges: [], metadata: {} };
    get.mockResolvedValue({ data: { id: "tpl-1", name: "One", description: "", graph_definition: graph } });
    const template = await fetchTemplate("tpl-1");
    expect(template.graph).toEqual(graph);
    expect(template.graph_text).toBeNull();
  });
});
