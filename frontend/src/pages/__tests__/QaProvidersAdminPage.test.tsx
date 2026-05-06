import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Stub the override hook so the page renders without a real
// QueryClientProvider. The page also uses useQueryClient() to invalidate
// queries on mutate — we substitute a mock client below.
const mockUseProviderOverrides = vi.fn();
vi.mock("@/hooks/useQaDashboard", () => ({
  useProviderOverrides: () => mockUseProviderOverrides(),
}));

const mockDelete = vi.fn();
vi.mock("@/api/qaDashboard", () => ({
  deleteProviderOverride: (repo: string) => mockDelete(repo),
  // upsertProviderOverride is reached via the form, not the page directly,
  // but the form imports it from the same module. Provide a no-op stub.
  upsertProviderOverride: vi.fn(),
}));

const mockInvalidate = vi.fn();
vi.mock("@tanstack/react-query", async () => {
  const actual = await vi.importActual<typeof import("@tanstack/react-query")>(
    "@tanstack/react-query",
  );
  return {
    ...actual,
    useQueryClient: () => ({
      invalidateQueries: mockInvalidate,
    }),
  };
});

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { QaProvidersAdminPage } from "../QaProvidersAdminPage";
import type { WorkspaceProviderOverride } from "@/lib/types";

const ROW_A: WorkspaceProviderOverride = {
  repo: "org/a",
  provider_type: "npm-workspaces-turbo",
  config: { affected_filter: "[HEAD^1]" },
  updated_at: new Date().toISOString(),
};
const ROW_B: WorkspaceProviderOverride = {
  repo: "org/b",
  provider_type: "pnpm-workspaces",
  config: { apps_glob: "apps/*" },
  updated_at: new Date().toISOString(),
};

beforeEach(() => {
  mockUseProviderOverrides.mockReset();
  mockDelete.mockReset();
  mockInvalidate.mockReset();
  // Auto-confirm window.confirm in delete tests.
  vi.spyOn(window, "confirm").mockImplementation(() => true);
});

describe("QaProvidersAdminPage", () => {
  it("renders empty state when no overrides", () => {
    mockUseProviderOverrides.mockReturnValue({
      data: [],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });

    render(<QaProvidersAdminPage />);
    expect(
      screen.getByText(/No overrides/i),
    ).toBeInTheDocument();
    // No list rendered.
    expect(screen.queryByTestId("provider-overrides-list")).toBeNull();
  });

  it("renders rows for existing overrides", () => {
    mockUseProviderOverrides.mockReturnValue({
      data: [ROW_A, ROW_B],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });

    render(<QaProvidersAdminPage />);
    expect(screen.getByTestId("provider-overrides-list")).toBeInTheDocument();
    expect(screen.getByTestId("provider-override-row-org/a")).toHaveTextContent(
      "npm-workspaces-turbo",
    );
    expect(screen.getByTestId("provider-override-row-org/b")).toHaveTextContent(
      "pnpm-workspaces",
    );
  });

  it("delete action calls deleteProviderOverride", async () => {
    mockUseProviderOverrides.mockReturnValue({
      data: [ROW_A],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    mockDelete.mockResolvedValue(undefined);

    render(<QaProvidersAdminPage />);
    fireEvent.click(screen.getByLabelText("Delete org/a"));

    await waitFor(() => expect(mockDelete).toHaveBeenCalledWith("org/a"));
    await waitFor(() => expect(mockInvalidate).toHaveBeenCalled());
  });
});
