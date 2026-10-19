import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchRepos = vi.fn();

vi.mock("@/api/repos", () => ({
  fetchRepos: () => mockFetchRepos(),
}));

vi.mock("@/hooks/useEnvironments", () => ({
  useGlobalEnv: () => ({ data: [], isLoading: false }),
  useSetGlobalEnv: () => ({ mutate: vi.fn() }),
  useRepoEnv: () => ({ data: [], isLoading: false }),
  useSetRepoEnv: () => ({ mutate: vi.fn() }),
  useRevealSecret: () => ({ mutateAsync: vi.fn() }),
}));

// DetectBanner and RepoPreferencesSection make their own network calls —
// stub them out; this test is about discoverability, not those panels.
vi.mock("@/components/environments/DetectBanner", () => ({
  DetectBanner: () => null,
}));
vi.mock("@/components/environments/RepoPreferencesSection", () => ({
  RepoPreferencesSection: () => null,
}));
vi.mock("@/components/environments/PullRequestReviewCard", () => ({
  PullRequestReviewCard: () => null,
}));

import { EnvironmentsPage } from "../EnvironmentsPage";

const REPOS = [
  {
    repo: "acme/backend",
    active: true,
    notes: "",
    created_at: "",
    updated_at: "",
    forge: "github" as const,
    has_qa_config: true,
    env_var_count: 2,
    env_secret_count: 2,
  },
  {
    repo: "acme/console",
    active: true,
    notes: "",
    created_at: "",
    updated_at: "",
    forge: "github" as const,
    has_qa_config: false,
    env_var_count: 22,
    env_secret_count: 20,
  },
];

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <EnvironmentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockFetchRepos.mockResolvedValue(REPOS);
});

describe("EnvironmentsPage — RAV-1294 discoverability", () => {
  it("surfaces repo-scoped variables from the Global landing tab", async () => {
    renderPage();
    const summary = await screen.findByTestId("repo-env-summary");
    expect(summary).toHaveTextContent("24 repository-scoped variables across 2 repositories");
    expect(
      within(summary).getByRole("button", { name: "acme/console (22)" }),
    ).toBeInTheDocument();
  });

  it("names its sections and buttons in sentence case and keeps the empty hints readable", async () => {
    renderPage();
    expect(screen.getByRole("heading", { name: "App config" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "QA test credentials" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add variable" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add QA variable" })).toBeInTheDocument();
    const hint = screen.getByText(/to set global defaults/);
    expect(hint.className).toContain("text-fg-muted");
    expect(hint.className).not.toContain("white/[0.12]");
  });

  it("clicking a repo chip jumps to that repo's tab", async () => {
    renderPage();
    const summary = await screen.findByTestId("repo-env-summary");
    await userEvent.click(
      within(summary).getByRole("button", { name: "acme/console (22)" }),
    );
    // The repo tab is active with the repo preselected in the picker.
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Repository" })).toHaveValue(
        "acme/console",
      ),
    );
  });

  it("offers a registry-fed picker instead of a free-text box", async () => {
    renderPage();
    await userEvent.click(screen.getByRole("tab", { name: "Repository" }));
    const picker = await screen.findByRole("combobox", { name: "Repository" });
    expect(
      within(picker).getByRole("option", { name: "acme/backend (2 vars, 2 secrets)" }),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText("owner/repo")).not.toBeInTheDocument();
  });
});
