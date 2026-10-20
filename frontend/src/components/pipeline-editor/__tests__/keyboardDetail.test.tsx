import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PipelineGraph } from "@/lib/types";

vi.mock("@/hooks/usePipelines", () => ({
  useRenameTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useTemplates: () => ({ data: [], isLoading: false }),
  useTemplate: () => ({ data: undefined, isLoading: false }),
  useDeleteTemplate: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [], isLoading: false }) }));

import { PipelineEditor } from "../PipelineEditor";

const graph: PipelineGraph = {
  graph_id: "g1",
  name: "quick-fix",
  nodes: [
    { node_id: "triage", agent_type: "triage", position: { x: 0, y: 0 } },
    { node_id: "developer", agent_type: "developer", position: { x: 240, y: 0 } },
  ],
  edges: [{ edge_id: "e1", source: "triage", target: "developer", edge_type: "flow" }],
  metadata: { max_total_budget_usd: 10, max_total_loops: 3, created_by: "manual" },
};

// React Flow draws an edge only between nodes it has measured, and jsdom
// has no layout: give every element a size and report it to the observer
// at once, so the canvas draws its edges as a browser would.
const offsets = ["offsetWidth", "offsetHeight"].map((key) => [key, Object.getOwnPropertyDescriptor(HTMLElement.prototype, key)] as const);
beforeAll(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(target: Element) {
        this.callback([{ target, contentRect: { width: 800, height: 600 } } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
      }
      unobserve() {}
      disconnect() {}
    },
  );
  for (const [key] of offsets) Object.defineProperty(HTMLElement.prototype, key, { configurable: true, get: () => 100 });
});
afterAll(() => {
  vi.unstubAllGlobals();
  for (const [key, descriptor] of offsets) if (descriptor) Object.defineProperty(HTMLElement.prototype, key, descriptor);
});

function renderEditor(initialGraph: PipelineGraph = graph) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PipelineEditor mode="page" initialGraph={initialGraph} />
    </QueryClientProvider>,
  );
}

// React Flow selects a focused node or edge on Enter or Space, but calls
// onNodeClick and onEdgeClick only for a pointer, so a keyboard could
// select a node yet never open its detail.
describe("the pipeline editor from the keyboard", () => {
  it.each([
    ["Enter", "{Enter}"],
    ["Space", " "],
  ])("opens a node's detail on %s and gives focus back to the node", async (_, keys) => {
    const user = userEvent.setup();
    const { container } = renderEditor();
    const node = container.querySelector<HTMLElement>('.react-flow__node[data-id="developer"]')!;
    node.focus();

    await user.keyboard(keys);

    const dialog = screen.getByRole("dialog", { name: "developer" });
    await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(node);
  });

  it.each([
    ["Enter", "{Enter}"],
    ["Space", " "],
  ])("opens an edge's detail on %s and gives focus back to the edge", async (_, keys) => {
    const user = userEvent.setup();
    const { container } = renderEditor();
    const edge = container.querySelector<SVGGElement>('.react-flow__edge[data-id="e1"]')!;
    edge.focus();
    expect(document.activeElement).toBe(edge);

    await user.keyboard(keys);

    const dialog = screen.getByRole("dialog", { name: "Flow edge from triage to developer" });
    await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));
    await user.keyboard("{Escape}");
    expect(document.activeElement).toBe(edge);
  });

  it("opens nothing for START or END, which have no detail", async () => {
    const user = userEvent.setup();
    const { container } = renderEditor();
    for (const id of ["__start__", "__end__"]) {
      container.querySelector<HTMLElement>(`.react-flow__node[data-id="${id}"]`)!.focus();
      await user.keyboard("{Enter} ");
      expect(screen.queryByRole("dialog")).toBeNull();
    }
  });
});

// A feedback edge's "max N loops" label opened the edge's detail, its click
// reaching the edge, but took no focus itself, so closing the detail left
// focus on <body>.
describe("a feedback edge's loop label", () => {
  const looped: PipelineGraph = {
    ...graph,
    edges: [
      ...graph.edges,
      {
        edge_id: "fb",
        source: "developer",
        target: "triage",
        edge_type: "feedback",
        loop_config: { max_loops: 3, feedback_mode: "result" },
      },
    ],
  };

  it("opens the loop's detail on a click and takes focus back when it closes", async () => {
    const user = userEvent.setup();
    renderEditor(looped);
    const label = screen.getByRole("button", { name: "Feedback loop from developer to triage, max 3 loops" });

    await user.click(label);

    const dialog = screen.getByRole("dialog", { name: "Feedback loop from developer to triage" });
    await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(label);
  });

  it("opens the loop's detail from the keyboard too", async () => {
    const user = userEvent.setup();
    renderEditor(looped);
    const label = screen.getByRole("button", { name: /max 3 loops/ });
    label.focus();

    await user.keyboard("{Enter}");

    expect(screen.getByRole("dialog", { name: "Feedback loop from developer to triage" })).toBeInTheDocument();
  });
});
