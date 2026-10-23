import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockUseTemplates = vi.fn();
const mockSetDefault = vi.fn();
const mockToastApiError = vi.hoisted(() => vi.fn());
vi.mock("@/hooks/usePipelines", () => ({
  useTemplates: () => mockUseTemplates(),
  useDuplicateTemplate: () => ({ mutate: vi.fn(), isPending: false }),
  useSetTemplateDefault: () => ({ mutate: mockSetDefault, isPending: false }),
}));
vi.mock("@/api/errorToast", () => ({ toastApiError: mockToastApiError }));
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
  mockSetDefault.mockReset();
  mockToastApiError.mockReset();
});

describe("PipelinesPage", () => {
  it("New pipeline is the shared primary button, in the same tone as every other primary action", () => {
    renderPage();
    const button = screen.getByRole("button", { name: /New pipeline/ });
    expect(button.className).toContain("bg-primary");
    expect(button.className).not.toContain("bg-blue-600");
  });

  it("New pipeline opens the editor", () => {
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: /New pipeline/ }));
    expect(screen.getByTestId("pipeline-editor")).toBeInTheDocument();
  });

  it("titles the saved templates in sentence case, with neutral icon tiles", () => {
    mockUseTemplates.mockReturnValue({
      data: [{ template_id: "t1", name: "Issue to PR", description: "", created_at: "2026-10-01T00:00:00Z" }],
      isLoading: false,
    });
    renderPage();
    const heading = screen.getByRole("heading", { level: 2 });
    expect(heading).toHaveTextContent(/^Saved templates/);
    expect(heading.className).toContain("text-section");
    const card = screen.getByRole("button", { name: /^Issue to PR/ });
    expect(card.innerHTML).not.toMatch(/text-info|bg-info|bg-blue/);
  });

  it("dates a template card in the shared format: no year inside the current one", () => {
    const year = new Date().getFullYear();
    mockUseTemplates.mockReturnValue({
      data: [{ template_id: "t1", name: "Issue to PR", description: "", created_at: `${year}-03-15T12:00:00Z` }],
      isLoading: false,
    });
    renderPage();
    const card = screen.getByRole("button", { name: /^Issue to PR/ });
    expect(card).toHaveTextContent(/Mar 15$/);
    expect(card).not.toHaveTextContent(String(year));
  });

  it("badges a built-in template and the default for a kind", () => {
    mockUseTemplates.mockReturnValue({
      data: [
        { template_id: "t1", name: "Issue to PR", description: "", created_at: "2026-10-01T00:00:00Z", is_builtin: true, default_for_kind: "code" },
        { template_id: "t2", name: "Nightly", description: "", created_at: "2026-10-01T00:00:00Z", is_builtin: false, default_for_kind: null },
      ],
      isLoading: false,
    });
    renderPage();
    const first = screen.getByRole("button", { name: /^Issue to PR/ });
    expect(first).toHaveTextContent("Built in");
    expect(first).toHaveTextContent("Default for code");
    expect(screen.getByRole("button", { name: /^Nightly/ })).not.toHaveTextContent("Default for");
  });

  it("opens a card's menu without opening the template", async () => {
    mockUseTemplates.mockReturnValue({
      data: [{ template_id: "t1", name: "Issue to PR", description: "", created_at: "2026-10-01T00:00:00Z" }],
      isLoading: false,
    });
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Make default for ops" }));
    expect(mockSetDefault).toHaveBeenCalledWith({ templateId: "t1", kind: "ops" }, expect.anything());
    expect(screen.queryByTestId("pipeline-editor")).toBeNull();
  });

  it("shows the server's reason when a default is taken", async () => {
    mockSetDefault.mockImplementation((_vars: unknown, opts: { onError: (e: unknown) => void }) =>
      opts.onError({ response: { status: 409, data: { detail: "another template is already the default for 'ops'; clear it first" } } }),
    );
    mockUseTemplates.mockReturnValue({
      data: [{ template_id: "t1", name: "Issue to PR", description: "", created_at: "2026-10-01T00:00:00Z" }],
      isLoading: false,
    });
    renderPage();
    await userEvent.click(screen.getByRole("button", { name: "Actions for Issue to PR" }));
    await userEvent.click(screen.getByRole("menuitem", { name: "Make default for ops" }));
    expect(mockToastApiError).toHaveBeenCalledWith(expect.objectContaining({ response: expect.objectContaining({ status: 409 }) }), "Could not change the default");
  });
});
