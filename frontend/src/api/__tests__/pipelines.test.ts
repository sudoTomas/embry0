import { describe, it, expect, vi, beforeEach } from "vitest";

// The pipelines API client maps between the backend's snake_case
// (`id`, `graph_definition`) and the frontend's existing types
// (`template_id`, `graph`). These tests pin that boundary so a future change
// in either direction surfaces here, not deep inside a page component.

vi.mock("../client", () => {
  return {
    api: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
    },
  };
});

import { api } from "../client";
import {
  createTemplate,
  duplicateTemplate,
  fetchTemplate,
  fetchTemplates,
  renameTemplate,
  setTemplateDefaultKind,
  validatePipeline,
} from "../pipelines";
import type { PipelineGraph } from "@/lib/types";

const SAMPLE_GRAPH: PipelineGraph = {
  graph_id: "g1",
  name: "test",
  nodes: [
    { node_id: "n1", agent_type: "developer", position: { x: 0, y: 0 } },
  ],
  edges: [],
  metadata: { max_total_budget_usd: 10, max_total_loops: 3, created_by: "auto" },
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("pipelines API client — boundary mappers", () => {
  it("fetchTemplates reads a bare array (no envelope) and maps id → template_id", async () => {
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: [
        {
          id: "abc",
          name: "quick-fix",
          description: "two steps",
          sandbox_profile: "slim",
          is_builtin: true,
          created_at: "2026-05-04T00:00:00Z",
          updated_at: "2026-05-04T00:00:00Z",
        },
      ],
    });
    const result = await fetchTemplates();
    expect(api.get).toHaveBeenCalledWith("/pipelines/templates");
    expect(result).toEqual([
      {
        template_id: "abc",
        name: "quick-fix",
        description: "two steps",
        created_at: "2026-05-04T00:00:00Z",
        sandbox_profile: "slim",
        is_builtin: true,
        default_for_kind: null,
      },
    ]);
  });

  it("fetchTemplate maps graph_definition → graph", async () => {
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: {
        id: "abc",
        name: "n",
        description: "d",
        sandbox_profile: null,
        graph_definition: SAMPLE_GRAPH,
        created_at: "2026-05-04T00:00:00Z",
        updated_at: "2026-05-04T00:00:00Z",
      },
    });
    const result = await fetchTemplate("abc");
    expect(api.get).toHaveBeenCalledWith("/pipelines/templates/abc");
    expect(result.template_id).toBe("abc");
    expect(result.graph).toEqual(SAMPLE_GRAPH);
  });

  it("fetchTemplate keeps is_builtin, false when the server leaves it out", async () => {
    const row = {
      id: "abc", name: "n", description: "d", sandbox_profile: null, graph_definition: SAMPLE_GRAPH,
      created_at: "2026-05-04T00:00:00Z", updated_at: "2026-05-04T00:00:00Z",
    };
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ data: { ...row, is_builtin: true } });
    expect((await fetchTemplate("abc")).is_builtin, "the editor must know a built-in is re-seeded").toBe(true);
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValueOnce({ data: row });
    expect((await fetchTemplate("abc")).is_builtin).toBe(false);
  });

  it("createTemplate sends graph under graph_definition", async () => {
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: {
        id: "new",
        name: "n",
        description: "d",
        sandbox_profile: null,
        graph_definition: SAMPLE_GRAPH,
        created_at: "2026-05-04T00:00:00Z",
        updated_at: "2026-05-04T00:00:00Z",
      },
    });
    await createTemplate("n", "d", SAMPLE_GRAPH);
    expect(api.post).toHaveBeenCalledWith("/pipelines/templates", {
      name: "n",
      description: "d",
      graph_definition: SAMPLE_GRAPH,
    });
  });

  it("createTemplate returns the write's warnings", async () => {
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: {
        id: "t1",
        name: "n",
        description: "d",
        sandbox_profile: null,
        graph_definition: SAMPLE_GRAPH,
        created_at: "2026-05-04T00:00:00Z",
        updated_at: "2026-05-04T00:00:00Z",
        warnings: ["superpowers:x is not available: source pending"],
      },
    });
    const created = await createTemplate("n", "d", SAMPLE_GRAPH);
    expect(created.warnings).toEqual(["superpowers:x is not available: source pending"]);
  });

  it("renameTemplate uses PUT and only sends provided fields", async () => {
    (api.put as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: {
        id: "abc",
        name: "renamed",
        description: "",
        sandbox_profile: null,
        graph_definition: SAMPLE_GRAPH,
        created_at: "x",
        updated_at: "y",
      },
    });
    await renameTemplate("abc", "renamed");
    expect(api.put).toHaveBeenCalledWith("/pipelines/templates/abc", {
      name: "renamed",
    });
  });

  it("renameTemplate maps graph → graph_definition when graph is provided", async () => {
    (api.put as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: {
        id: "abc",
        name: "n",
        description: "d",
        sandbox_profile: null,
        graph_definition: SAMPLE_GRAPH,
        created_at: "x",
        updated_at: "y",
      },
    });
    await renameTemplate("abc", undefined, undefined, SAMPLE_GRAPH);
    expect(api.put).toHaveBeenCalledWith("/pipelines/templates/abc", {
      graph_definition: SAMPLE_GRAPH,
    });
  });

  it("validatePipeline posts the graph directly (no envelope)", async () => {
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: { valid: true, errors: [] },
    });
    await validatePipeline(SAMPLE_GRAPH);
    expect(api.post).toHaveBeenCalledWith("/pipelines/validate", SAMPLE_GRAPH);
  });

  it("fetchTemplates keeps is_builtin, default_for_kind and sandbox_profile", async () => {
    (api.get as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: [{ id: "abc", name: "quick-fix", description: "", sandbox_profile: "node", is_builtin: true, default_for_kind: "code", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" }],
    });
    const [row] = await fetchTemplates();
    expect(row).toMatchObject({ template_id: "abc", is_builtin: true, default_for_kind: "code", sandbox_profile: "node" });
  });

  it("duplicateTemplate posts the new name and maps the copy", async () => {
    (api.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      data: { id: "def", name: "quick-fix (copy)", description: "", sandbox_profile: null, graph_definition: SAMPLE_GRAPH, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z" },
    });
    await expect(duplicateTemplate("abc", "quick-fix (copy)")).resolves.toMatchObject({ template_id: "def", name: "quick-fix (copy)" });
    expect(api.post).toHaveBeenCalledWith("/pipelines/templates/abc/duplicate", { name: "quick-fix (copy)" });
  });

  it("setTemplateDefaultKind sends the kind, and an explicit null to clear it", async () => {
    (api.put as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { id: "abc", name: "quick-fix", description: "", sandbox_profile: null, graph_definition: SAMPLE_GRAPH, default_for_kind: null, created_at: "", updated_at: "" },
    });
    await setTemplateDefaultKind("abc", "research");
    expect(api.put).toHaveBeenLastCalledWith("/pipelines/templates/abc", { default_for_kind: "research" });
    await setTemplateDefaultKind("abc", null);
    expect(api.put).toHaveBeenLastCalledWith("/pipelines/templates/abc", { default_for_kind: null });
  });
});
