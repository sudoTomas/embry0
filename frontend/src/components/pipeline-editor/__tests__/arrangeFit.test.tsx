import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Node, ReactFlowProps } from "@xyflow/react";
import type { PipelineGraph } from "@/lib/types";

vi.mock("@/hooks/usePipelines", () => ({
  useRenameTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useTemplates: () => ({ data: [], isLoading: false }),
  useTemplate: () => ({ data: undefined, isLoading: false }),
  useDeleteTemplate: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [], isLoading: false }) }));

// jsdom has no layout, so the canvas is not drawn: the stand-in keeps the
// nodes the editor hands React Flow, and the fit records which nodes React
// Flow held when it was asked for.
const flow: { props?: ReactFlowProps; fitted: (Node[] | undefined)[] } = { fitted: [] };
const fitView = vi.fn(async () => {
  flow.fitted.push(flow.props?.nodes);
  return true;
});
vi.mock("@xyflow/react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@xyflow/react")>()),
  ReactFlow: (props: ReactFlowProps) => {
    flow.props = props;
    return null;
  },
  useReactFlow: () => ({ screenToFlowPosition: vi.fn(), fitView }),
}));

import { PipelineEditor } from "../PipelineEditor";

const media = (matches: boolean) => (query: string) => ({
  matches,
  media: query,
  addEventListener: () => {},
  removeEventListener: () => {},
});

const graph: PipelineGraph = {
  graph_id: "g1",
  name: "quick-fix",
  nodes: [
    { node_id: "triage", agent_type: "triage", position: { x: 0, y: 0 } },
    { node_id: "developer", agent_type: "developer", position: { x: 240, y: 0 } },
    { node_id: "reviewer", agent_type: "reviewer", position: { x: 480, y: 0 } },
  ],
  edges: [
    { edge_id: "e1", source: "triage", target: "developer", edge_type: "flow" },
    { edge_id: "e2", source: "developer", target: "reviewer", edge_type: "flow" },
  ],
  metadata: { max_total_budget_usd: 10, max_total_loops: 3, created_by: "manual" },
};

const open = () =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <PipelineEditor mode="page" initialGraph={graph} />
    </QueryClientProvider>,
  );

afterEach(() => {
  fitView.mockClear();
  flow.fitted = [];
  vi.unstubAllGlobals();
});

// The fitView prop fits the canvas once, as it opens. Auto arrange moved
// every node and left the view where it was, so on quick-fix at 1440 px the
// arranged triage and START sat above the canvas, under the toolbar.
describe("PipelineEditor arrange fit", () => {
  it("refits the view to the arranged nodes after each arrange", async () => {
    const user = userEvent.setup();
    open();
    const opened = flow.props?.nodes;
    expect(fitView, "the opening fit is the fitView prop's").not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Auto arrange" }));
    expect(fitView).toHaveBeenCalledTimes(1);
    // The fit is asked for once React Flow has the arranged nodes, not the
    // ones the arrange replaced.
    expect(flow.fitted[0]).not.toBe(opened);
    expect(flow.fitted[0]).toBe(flow.props?.nodes);

    await user.click(screen.getByRole("button", { name: "Auto arrange" }));
    expect(fitView).toHaveBeenCalledTimes(2);
  });

  it("lands the fit at once under reduced motion", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("matchMedia", media(true));
    open();
    await user.click(screen.getByRole("button", { name: "Auto arrange" }));
    expect(fitView).toHaveBeenCalledWith(expect.objectContaining({ duration: 0 }));
  });

  it("glides the fit when motion is allowed", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("matchMedia", media(false));
    open();
    await user.click(screen.getByRole("button", { name: "Auto arrange" }));
    const [[options]] = fitView.mock.calls as unknown as [[{ duration: number }]];
    expect(options.duration).toBeGreaterThan(0);
  });
});
