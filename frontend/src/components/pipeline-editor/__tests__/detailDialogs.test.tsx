import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Edge } from "@xyflow/react";
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
import { EdgeDetailPopup } from "../EdgeDetailPopup";

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

// The edge detail had no dialog role, no name, no trap and no Escape: a
// screen reader heard an unnamed group of controls, Tab walked out of it
// to the canvas behind, and Escape left it open. The focus-trap tests'
// dialog table pins Escape and the return of focus to the opener.
describe("the pipeline editor's edge detail", () => {
  const flow = { id: "e1", source: "triage", target: "developer", data: { edgeType: "flow" } } as Edge;
  const feedback = { id: "e2", source: "reviewer", target: "developer", data: { edgeType: "feedback" } } as Edge;

  it("is a dialog named by its kind and the two nodes it joins", () => {
    const { unmount } = render(<EdgeDetailPopup edge={flow} onUpdate={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog", { name: "Flow edge from triage to developer" })).toHaveAttribute("aria-modal", "true");
    unmount();

    render(<EdgeDetailPopup edge={feedback} onUpdate={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog", { name: "Feedback loop from reviewer to developer" })).toHaveAttribute("aria-modal", "true");
  });

  it("keeps Tab inside itself, away from the canvas behind it", async () => {
    const user = userEvent.setup();
    render(
      <>
        <button type="button">Behind</button>
        <EdgeDetailPopup edge={feedback} onUpdate={vi.fn()} onClose={vi.fn()} />
        <button type="button">Below</button>
      </>,
    );
    const dialog = screen.getByRole("dialog");
    await waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));

    const visited = new Set<Element>();
    for (let i = 0; i < 12; i++) {
      await user.tab({ shift: i >= 6 });
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
      visited.add(document.activeElement!);
    }
    // Close, the three loop fields, Cancel and Save.
    expect(visited.size).toBe(6);
  });
});
