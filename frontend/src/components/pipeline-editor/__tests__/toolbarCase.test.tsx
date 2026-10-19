import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("@/hooks/usePipelines", () => ({
  useRenameTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useTemplates: () => ({ data: [], isLoading: false }),
  useTemplate: () => ({ data: undefined, isLoading: false }),
  useDeleteTemplate: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [], isLoading: false }) }));

import { PipelineEditor } from "../PipelineEditor";

function renderEditor(mode: "page" | "modal") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PipelineEditor mode={mode} />
    </QueryClientProvider>,
  );
}

// The editor's own controls follow the family's sentence case.
describe("PipelineEditor toolbar", () => {
  it("names the page toolbar's layout action in sentence case", () => {
    renderEditor("page");
    expect(screen.getByRole("button", { name: "Auto arrange" })).toBeInTheDocument();
  });

  it("titles the modal editor and its actions in sentence case", () => {
    renderEditor("modal");
    expect(screen.getByRole("heading", { name: "Pipeline editor" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Auto arrange" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load template" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply pipeline" })).toBeInTheDocument();
  });

  it("titles the template picker in sentence case", () => {
    renderEditor("modal");
    fireEvent.click(screen.getByRole("button", { name: "Load template" }));
    expect(screen.getByRole("heading", { name: "Load pipeline template" })).toBeInTheDocument();
  });
});
