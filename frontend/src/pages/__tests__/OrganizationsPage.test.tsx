import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchOrgs = vi.fn();
vi.mock("@/api/orgs", async () => {
  const actual = await vi.importActual<typeof import("@/api/orgs")>("@/api/orgs");
  return { ...actual, fetchOrganizations: () => mockFetchOrgs() };
});
const mockFetchCredentials = vi.fn();
vi.mock("@/api/credentials", () => ({ fetchCredentials: () => mockFetchCredentials() }));

import { OrganizationsPage } from "../OrganizationsPage";
import { org } from "@/components/orgs/__tests__/fixtures";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/orgs"]}>
        <Routes>
          <Route path="/orgs" element={<OrganizationsPage />} />
          <Route path="/orgs/-/new" element={<div data-testid="wizard" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockFetchOrgs.mockReset();
  mockFetchCredentials.mockReset().mockResolvedValue([{ id: "cred-1", label: "ci bot" }]);
});

describe("OrganizationsPage", () => {
  it("lists each organization as a table row", async () => {
    mockFetchOrgs.mockResolvedValue([
      org({ discovered_at: null }),
      org({
        owner: "widgets-group",
        forge: "gitlab",
        credential_id: "cred-1",
        access: { read: true, hooks: null, push: null, detail: "" },
        repo_count: null,
        status: "paused",
      }),
    ]);
    renderPage();
    const rows = await screen.findAllByTestId("org-row");
    expect(rows).toHaveLength(2);

    expect(within(rows[0]).getByTestId("org-link")).toHaveAttribute("href", "/orgs/acme");
    expect(rows[0]).toHaveTextContent("GitHub");
    expect(rows[0]).toHaveTextContent("Environment token");
    expect(within(rows[0]).getByText("GITHUB_TOKEN__ACME").className).toContain("font-mono");
    expect(within(rows[0]).getByTestId("access-read")).toHaveAttribute("data-state", "yes");
    expect(within(rows[0]).getByTestId("access-hooks")).toHaveAttribute("data-state", "yes");
    expect(within(rows[0]).getByTestId("access-push")).toHaveAttribute("data-state", "unknown");
    expect(rows[0]).toHaveTextContent("12");
    expect(rows[0]).toHaveTextContent("never");
    expect(rows[0]).toHaveTextContent("Active");

    expect(rows[1]).toHaveTextContent("GitLab");
    expect(await within(rows[1]).findByText("ci bot")).toBeInTheDocument();
    expect(rows[1]).toHaveTextContent("Paused");
    expect(screen.getByTestId("add-org-cta")).toHaveAttribute("href", "/orgs/-/new");
  });

  it("shows an EmptyState with the add button when there is none", async () => {
    mockFetchOrgs.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText("No organizations yet")).toBeInTheDocument();
    expect(screen.queryByTestId("orgs-table")).toBeNull();
    expect(screen.getAllByTestId("add-org-cta").length).toBeGreaterThan(0);
  });

  it("shows a skeleton while loading", () => {
    mockFetchOrgs.mockReturnValue(new Promise(() => {}));
    renderPage();
    expect(screen.getByTestId("orgs-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("orgs-table")).toBeNull();
  });
});
