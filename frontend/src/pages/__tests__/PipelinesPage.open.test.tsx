// Opening a saved template from the Pipelines list must open that template:
// its name, Save (not only Save As) enabled for a template of your own, and
// Start and End connected to the graph, which stores no scaffold edges of its
// own. A built-in template is re-seeded on every restart, so Save in place
// would be lost: it opens with Save off and Save As as the action to take.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PipelineTemplate } from "@/lib/types";

const node = (node_id: string, agent_type: string, x: number) => ({
  node_id, agent_type, label: agent_type, position: { x, y: 0 },
});
const graph = (name: string) =>
  ({
    graph_id: `g-${name}`,
    name,
    nodes: [node("triage", "triage", 0), node("developer", "developer", 200), node("review", "review", 400)],
    edges: [
      { edge_id: "e1", source: "triage", target: "developer", edge_type: "flow" },
      { edge_id: "e2", source: "developer", target: "review", edge_type: "flow" },
    ],
    metadata: { created_by: "manual", max_total_budget_usd: 20, max_total_loops: 10 },
  }) as PipelineTemplate["graph"];

const custom: PipelineTemplate = {
  template_id: "tpl-1",
  name: "Quick fix",
  description: "",
  created_at: "2026-10-01T00:00:00Z",
  is_builtin: false,
  graph: graph("Quick fix"),
};
const builtin: PipelineTemplate = {
  template_id: "tpl-b",
  name: "Bug fix",
  description: "",
  created_at: "2026-10-01T00:00:00Z",
  is_builtin: true,
  graph: graph("Bug fix"),
};
const BY_ID: Record<string, PipelineTemplate> = { [custom.template_id]: custom, [builtin.template_id]: builtin };
const summary = ({ template_id, name, description, created_at }: PipelineTemplate) => ({
  template_id, name, description, created_at,
});

const rename = vi.fn();
const create = vi.fn();
vi.mock("@/hooks/usePipelines", () => ({
  useTemplates: () => ({ data: [summary(custom), summary(builtin)], isLoading: false }),
  useTemplate: (id: string | null) => ({ data: id ? BY_ID[id] : undefined }),
  useRenameTemplate: () => ({ mutate: rename, isPending: false }),
  useCreateTemplate: () => ({ mutate: create, isPending: false }),
  useDeleteTemplate: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [] }) }));
vi.mock("@/api/pipelines", () => ({ fetchTemplate: vi.fn(async (id: string) => BY_ID[id]) }));

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

async function openFromList(name: RegExp) {
  renderPage();
  fireEvent.click(screen.getByRole("button", { name }));
  return screen.findByPlaceholderText("Pipeline name...");
}

const saveButton = () => screen.getByRole("button", { name: /^Save$/ });
const saveAsButton = () => screen.getByRole("button", { name: /^Save As$/ });

beforeEach(() => {
  rename.mockReset();
  create.mockReset();
});

describe("opening a saved pipeline", () => {
  it("opens a template of your own under its name with Save enabled", async () => {
    const name = await openFromList(/Quick fix/);
    expect(name).toHaveValue("Quick fix");
    const save = saveButton();
    expect(save, "a saved template must be savable in place, not only as a copy").toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(rename).toHaveBeenCalled());
    expect(rename.mock.calls[0][0]).toMatchObject({ templateId: "tpl-1", name: "Quick fix" });
  });

  it("opens a built-in under its name with Save off and says why", async () => {
    const name = await openFromList(/Bug fix/);
    expect(name).toHaveValue("Bug fix");
    expect(saveButton(), "a built-in is re-seeded on restart, so Save in place would be lost").toBeDisabled();
    expect(saveButton()).toHaveAccessibleDescription(/Built-in templates reset on restart\. Save As makes your own copy\./);
    expect(screen.getByText("Built-in templates reset on restart. Save As makes your own copy.")).toBeInTheDocument();
    fireEvent.click(saveButton());
    expect(rename).not.toHaveBeenCalled();
  });

  it("connects Start and End to the graph it loads", async () => {
    await openFromList(/Quick fix/);
    expect(screen.queryByText("Connect Start → End")).toBeNull();
    expect(screen.getByText("Valid")).toBeInTheDocument();
  });
});

// Golden touch: one gold action per view, the one to take. A template of your
// own is saved in place; a new pipeline and a built-in can only be kept as a
// template of your own, with Save As.
describe("the pipeline editor's gold action", () => {
  it("is Save for a template of your own, with Save As beside it", async () => {
    await openFromList(/Quick fix/);
    expect(saveButton()).toHaveClass("bg-primary");
    expect(saveAsButton()).not.toHaveClass("bg-primary");
  });

  it("is Save As for a built-in", async () => {
    await openFromList(/Bug fix/);
    expect(saveAsButton()).toHaveClass("bg-primary");
    expect(saveButton()).not.toHaveClass("bg-primary");
  });

  it("is Save As for a new pipeline, which has nothing to save in place", async () => {
    renderPage();
    fireEvent.click(screen.getByTestId("new-pipeline"));
    await screen.findByPlaceholderText("Pipeline name...");
    expect(saveAsButton()).toHaveClass("bg-primary");
    expect(saveButton()).not.toHaveClass("bg-primary");
  });

  it("moves to Save once Save As has made a copy of a built-in", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("My bug fix");
    create.mockImplementation((_vars, opts) => opts.onSuccess({ ...builtin, template_id: "tpl-mine", name: "My bug fix", is_builtin: false }));
    await openFromList(/Bug fix/);
    fireEvent.click(saveAsButton());
    await waitFor(() => expect(saveButton()).toBeEnabled());
    expect(saveButton()).toHaveClass("bg-primary");
    expect(screen.queryByText(/Built-in templates reset on restart/)).toBeNull();
  });
});

describe("picking a template in the editor's Templates drawer", () => {
  it("keeps Save off for a built-in and on for a template of your own", async () => {
    renderPage();
    fireEvent.click(screen.getByTestId("new-pipeline"));
    await screen.findByPlaceholderText("Pipeline name...");

    fireEvent.click(screen.getByRole("button", { name: /Templates/ }));
    fireEvent.click(await screen.findByText("Bug fix"));
    await waitFor(() => expect(screen.getByPlaceholderText("Pipeline name...")).toHaveValue("Bug fix"));
    expect(saveButton(), "a built-in picked in the drawer is no more savable in place").toBeDisabled();
    expect(saveAsButton()).toHaveClass("bg-primary");

    fireEvent.click(screen.getByRole("button", { name: /Templates/ }));
    fireEvent.click(await screen.findByText("Quick fix"));
    await waitFor(() => expect(screen.getByPlaceholderText("Pipeline name...")).toHaveValue("Quick fix"));
    expect(saveButton()).toBeEnabled();
    expect(saveButton()).toHaveClass("bg-primary");
  });
});
