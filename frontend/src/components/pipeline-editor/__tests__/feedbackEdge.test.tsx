import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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

const looped: PipelineGraph = {
  graph_id: "g1",
  name: "quick-fix",
  nodes: [
    { node_id: "triage", agent_type: "triage", position: { x: 0, y: 0 } },
    { node_id: "developer", agent_type: "developer", position: { x: 240, y: 0 } },
  ],
  edges: [
    { edge_id: "e1", source: "triage", target: "developer", edge_type: "flow" },
    {
      edge_id: "fb",
      source: "developer",
      target: "triage",
      edge_type: "feedback",
      loop_config: { max_loops: 3, feedback_mode: "result" },
    },
  ],
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

function renderEditor() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <PipelineEditor mode="page" initialGraph={looped} />
    </QueryClientProvider>,
  );
}

// The feedback edge drew in a hard-coded red, and its label sat on its own
// dashed line with a 12% red wash, so the line struck through "max 3 loops".
describe("FeedbackEdge", () => {
  it("strokes the loop in the destructive token", () => {
    const { container } = renderEditor();
    const path = container.querySelector('[data-id="fb"] path.react-flow__edge-path') as SVGPathElement;
    expect(path.style.stroke).toBe("var(--color-destructive)");
  });

  it("sets the label on the opaque surface, so the line stops at it", () => {
    renderEditor();
    const label = screen.getByRole("button", { name: "Feedback loop from developer to triage, max 3 loops" });
    // Its colours come from classes on the tokens: no inline colour, and
    // the raised surface, which is opaque, under the text.
    expect(label.style.background).toBe("");
    expect(label.style.color).toBe("");
    expect(label).toHaveClass("bg-surface", "text-destructive");
  });
});
