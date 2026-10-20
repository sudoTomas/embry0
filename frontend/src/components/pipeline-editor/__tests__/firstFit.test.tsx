import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { getViewportForBounds, type ReactFlowProps } from "@xyflow/react";

vi.mock("@/hooks/usePipelines", () => ({
  useRenameTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useTemplates: () => ({ data: [], isLoading: false }),
  useTemplate: () => ({ data: undefined, isLoading: false }),
  useDeleteTemplate: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [], isLoading: false }) }));

// jsdom has no layout, so the canvas is not drawn: the stand-in keeps the
// props the editor hands React Flow, and the fit is worked out from them
// with React Flow's own arithmetic.
const flow: { props?: ReactFlowProps } = {};
vi.mock("@xyflow/react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@xyflow/react")>()),
  ReactFlow: (props: ReactFlowProps) => {
    flow.props = props;
    return null;
  },
}));

import { PipelineEditor } from "../PipelineEditor";

/** React Flow's limits when a prop is left out. */
const DEFAULT_MIN_ZOOM = 0.5;
const DEFAULT_MAX_ZOOM = 2;

describe("PipelineEditor first fit", () => {
  // A seven-agent pipeline, START to END, lays out 1524 px wide. At 390 px
  // the canvas is 350 px by 509, and React Flow's default floor of 0.5 left
  // START and END off its sides with zoom-out already spent.
  it.each([
    [360, 320],
    [390, 350],
  ])("fits a seven-agent pipeline on a %i px phone", (_, canvasWidth) => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <PipelineEditor mode="page" />
      </QueryClientProvider>,
    );
    const { fitView, minZoom = DEFAULT_MIN_ZOOM, maxZoom = DEFAULT_MAX_ZOOM } = flow.props!;
    expect(fitView).toBe(true);
    const graph = { x: 0, y: 0, width: 1524, height: 150 };
    const { x, zoom } = getViewportForBounds(graph, canvasWidth, 509, minZoom, maxZoom, 0.1);
    expect(x, "START off the left edge").toBeGreaterThanOrEqual(0);
    expect(x + graph.width * zoom, "END off the right edge").toBeLessThanOrEqual(canvasWidth);
  });
});
