import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockUseQaRepos = vi.fn();
vi.mock("@/hooks/useQaDashboard", () => ({
  useQaRepos: () => mockUseQaRepos(),
}));

vi.mock("@/components/qa/QaRepoCard", () => ({
  QaRepoCard: ({ repo }: { repo: { repo: string } }) => (
    <tr>
      <td data-testid="qa-repo-card">{repo.repo}</td>
    </tr>
  ),
}));

import { QaReposPage } from "../QaReposPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <QaReposPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockUseQaRepos.mockReset();
});

describe("QaReposPage (QA runs only since RAV-1301)", () => {
  it("renders run cards and links to the central /repos page", () => {
    mockUseQaRepos.mockReturnValue({
      data: [{ repo: "acme/with-runs" }],
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });
    renderPage();

    expect(screen.getByTestId("qa-repo-card")).toHaveTextContent("acme/with-runs");
    expect(screen.getByTestId("add-repo-cta")).toHaveAttribute("href", "/repos");
    // Registration moved to /repos — no free-text form here anymore.
    expect(screen.queryByTestId("register-repo-input")).not.toBeInTheDocument();
    expect(screen.queryByTestId("registered-repos-list")).not.toBeInTheDocument();
  });

  it("empty state carries the /repos CTA", () => {
    mockUseQaRepos.mockReturnValue({ data: [], isLoading: false, isError: false, refetch: vi.fn() });
    renderPage();
    expect(screen.getByText("No QA runs yet")).toBeInTheDocument();
    // Header + empty-state both carry the CTA.
    const ctas = screen.getAllByTestId("add-repo-cta");
    expect(ctas).toHaveLength(2);
    for (const cta of ctas) expect(cta).toHaveAttribute("href", "/repos");
  });
});
