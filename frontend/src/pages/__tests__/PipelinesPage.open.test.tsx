// Opening a saved template from the Pipelines list must open that template:
// its name, Save (not only Save As) enabled, and Start and End connected to
// the graph, which stores no scaffold edges of its own.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PipelineTemplate } from "@/lib/types";

const rename = vi.fn();
vi.mock("@/hooks/usePipelines", () => ({
  useTemplates: () => ({
    data: [{ template_id: "tpl-1", name: "Quick fix", description: "", created_at: "2026-10-01T00:00:00Z" }],
    isLoading: false,
  }),
  useRenameTemplate: () => ({ mutate: rename, isPending: false }),
  useCreateTemplate: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [] }) }));

const node = (node_id: string, agent_type: string, x: number) => ({
  node_id, agent_type, label: agent_type, position: { x, y: 0 },
});
const template: PipelineTemplate = {
  template_id: "tpl-1",
  name: "Quick fix",
  description: "",
  created_at: "2026-10-01T00:00:00Z",
  graph: {
    graph_id: "g-1",
    name: "Quick fix",
    nodes: [node("triage", "triage", 0), node("developer", "developer", 200), node("review", "review", 400)],
    edges: [
      { edge_id: "e1", source: "triage", target: "developer", edge_type: "flow" },
      { edge_id: "e2", source: "developer", target: "review", edge_type: "flow" },
    ],
    metadata: { created_by: "manual", max_total_budget_usd: 20, max_total_loops: 10 },
  } as PipelineTemplate["graph"],
};
vi.mock("@/api/pipelines", () => ({ fetchTemplate: vi.fn(async () => template) }));

import { PipelinesPage } from "../PipelinesPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PipelinesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => rename.mockReset());

describe("opening a saved pipeline", () => {
  it("opens under its own name with Save enabled", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /Quick fix/ }));
    const name = await screen.findByPlaceholderText("Pipeline name...");
    expect(name).toHaveValue("Quick fix");
    const save = screen.getByRole("button", { name: /^Save$/ });
    expect(save, "a saved template must be savable in place, not only as a copy").toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(rename).toHaveBeenCalled());
    expect(rename.mock.calls[0][0]).toMatchObject({ templateId: "tpl-1", name: "Quick fix" });
  });

  it("connects Start and End to the graph it loads", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /Quick fix/ }));
    await screen.findByPlaceholderText("Pipeline name...");
    expect(screen.queryByText("Connect Start → End")).toBeNull();
    expect(screen.getByText("Valid")).toBeInTheDocument();
  });
});

// Golden touch: one gold action per view, the one to take. A saved pipeline
// is saved in place; a new one can only be saved as a template.
describe("the pipeline editor's gold action", () => {
  it("is Save for an opened saved pipeline, with Save As beside it", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /Quick fix/ }));
    await screen.findByPlaceholderText("Pipeline name...");
    expect(screen.getByRole("button", { name: /^Save$/ })).toHaveClass("bg-primary");
    expect(screen.getByRole("button", { name: /^Save As$/ })).not.toHaveClass("bg-primary");
  });

  it("is Save As for a new pipeline, which has nothing to save in place", async () => {
    renderPage();
    fireEvent.click(screen.getByTestId("new-pipeline"));
    await screen.findByPlaceholderText("Pipeline name...");
    expect(screen.getByRole("button", { name: /^Save As$/ })).toHaveClass("bg-primary");
    expect(screen.getByRole("button", { name: /^Save$/ })).not.toHaveClass("bg-primary");
  });
});
