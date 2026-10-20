import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Edge, Node } from "@xyflow/react";
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
import { AgentDetailPopup } from "../AgentDetailPopup";

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
  // It had the dialog role but no name, so a screen reader announced an
  // unnamed dialog; the edge detail already carried one.
  it("is a dialog named by the agent it shows", () => {
    const agent = (data: Record<string, unknown>) => ({ id: "n1", position: { x: 0, y: 0 }, data }) as Node;
    const { unmount } = render(<AgentDetailPopup node={agent({ agentType: "developer" })} onUpdate={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog", { name: "Developer" })).toHaveAttribute("aria-modal", "true");
    unmount();

    render(<AgentDetailPopup node={agent({ agentType: "developer", label: "Lead dev" })} onUpdate={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByRole("dialog", { name: "Lead dev" })).toBeInTheDocument();
  });

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

  // The header column is a flex column, which stretches its children: the
  // badge drew as wide as the source and target row (165 px for a 64 px
  // label, measured at 390 and 1440 px). jsdom has no layout, so this pins
  // the badge's own alignment, the fix, rather than its width.
  it.each([
    ["flow", flow, "Flow Edge"],
    ["feedback", feedback, "Feedback Loop"],
  ] as const)("keeps the %s badge to its own width", (_, edge, text) => {
    render(<EdgeDetailPopup edge={edge} onUpdate={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText(text)).toHaveClass("self-start");
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
