import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

function renderEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PipelineEditor mode="page" initialGraph={graph} />
    </QueryClientProvider>,
  );
}

describe("the pipeline editor's agent detail", () => {
  it("closes on Escape and gives focus back to the node that opened it", async () => {
    const user = userEvent.setup();
    const { container } = renderEditor();
    const node = container.querySelector<HTMLElement>('.react-flow__node[data-id="developer"]')!;
    // A press focuses the node (React Flow gives it tabindex 0) and the click
    // opens its detail. user-event's mousedown carries no window, which the
    // node's drag handler reads, so the test does the two steps itself.
    node.focus();
    fireEvent.click(node);
    const dialog = screen.getByRole("dialog");
    await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(node);
  });
});
