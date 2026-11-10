// A template whose graph is too deeply nested for the API to send as an object arrives as its JSON text (#431):
// opening it shows that text read-only, and loading or copying it says so, rather than the editor crashing on it.
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PipelineTemplate } from "@/lib/types";

const deep: PipelineTemplate = {
  template_id: "tpl-deep",
  name: "Deep one",
  description: "",
  created_at: "2026-10-01T00:00:00Z",
  is_builtin: false,
  graph: null,
  graph_text: '{"nodes": [], "notes": {"a": {"a": "leaf"}}}',
};

vi.mock("@/hooks/usePipelines", () => ({
  useTemplates: () => ({
    data: [{ template_id: deep.template_id, name: deep.name, description: "", created_at: deep.created_at }],
    isLoading: false,
  }),
  useTemplate: () => ({ data: deep }),
  useRenameTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useDuplicateTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useSetTemplateDefault: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [] }) }));
vi.mock("@/api/pipelines", () => ({ fetchTemplate: vi.fn(async () => deep) }));
const toastError = vi.hoisted(() => vi.fn());
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: toastError, warning: vi.fn() } }));

import { PipelinesPage } from "../PipelinesPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PipelinesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PipelinesPage — a graph stored as text (#431)", () => {
  it("opens it read-only with the note, not in the editor", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /^Deep one/ }));

    const shown = await screen.findByTestId("stored-as-text");
    expect(shown).toHaveTextContent("Too deeply nested to show; stored as text.");
    expect(shown).toHaveTextContent('"notes"');
    expect(screen.queryByPlaceholderText("Pipeline name..."), "the editor is not opened on it").toBeNull();
  });

  it("says so when the editor's template drawer loads it, and keeps the canvas", async () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /New pipeline/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Templates" }));
    fireEvent.click(await screen.findByText("Deep one"));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Deep one: Too deeply nested to show; stored as text."),
    );
    expect(screen.getByPlaceholderText("Pipeline name..."), "the editor stays open on its canvas").toBeInTheDocument();
  });
});

describe("PipelineEditor in a dialog — a graph stored as text (#431)", () => {
  it("says so when Load template picks it, and keeps the canvas", async () => {
    const { PipelineEditor } = await import("@/components/pipeline-editor/PipelineEditor");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <PipelineEditor mode="modal" onApply={vi.fn()} onClose={vi.fn()} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Load template" }));
    fireEvent.click(await screen.findByRole("button", { name: /Deep one/ }));
    const picker = screen.getByRole("dialog", { name: "Load pipeline template" });
    fireEvent.click(within(picker).getByRole("button", { name: "Load template" }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Deep one: Too deeply nested to show; stored as text."),
    );
  });
});
