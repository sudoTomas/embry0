import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockUseTemplates = vi.fn();
vi.mock("@/hooks/usePipelines", () => ({
  useTemplates: () => mockUseTemplates(),
}));
vi.mock("@/api/pipelines", () => ({
  fetchTemplate: vi.fn(),
}));
vi.mock("@/components/pipeline-editor/PipelineEditor", () => ({
  PipelineEditor: () => <div data-testid="pipeline-editor" />,
}));

import { PipelinesPage } from "../PipelinesPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PipelinesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockUseTemplates.mockReset();
  mockUseTemplates.mockReturnValue({ data: [], isLoading: false });
});

describe("PipelinesPage", () => {
  it("New Pipeline is the shared primary button, in the same tone as every other primary action", () => {
    renderPage();
    const button = screen.getByRole("button", { name: /New Pipeline/ });
    expect(button.className).toContain("bg-primary");
    expect(button.className).not.toContain("bg-blue-600");
  });

  it("New Pipeline opens the editor", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /New Pipeline/ }));
    expect(screen.getByTestId("pipeline-editor")).toBeInTheDocument();
  });
});
