import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
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

const ripples = () => document.querySelectorAll(".divine-ripple-circle");

// The gold ring over Auto arrange acknowledges an arrange. It was mounted
// with the editor, so opening a template rang it over a button nobody had
// pressed.
describe("PipelineEditor arrange ripple", () => {
  it.each(["page", "modal"] as const)("rings only after an arrange in the %s editor", async (mode) => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <PipelineEditor mode={mode} initialGraph={graph} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(ripples()).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Auto arrange" }));
    expect(ripples()).toHaveLength(1);
    const first = ripples()[0];

    // Each arrange rings anew: a fresh ring, never a second one.
    await user.click(screen.getByRole("button", { name: "Auto arrange" }));
    expect(ripples()).toHaveLength(1);
    expect(ripples()[0]).not.toBe(first);
  });
});
