import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchRepos = vi.fn();
const mockPushRepo = vi.fn();
const mockPushRepoPr = vi.fn();
const mockMergeRepoPr = vi.fn();

vi.mock("@/api/agent", () => ({
  fetchRepos: () => mockFetchRepos(),
  pushRepo: (slug: string) => mockPushRepo(slug),
  pushRepoPr: (slug: string) => mockPushRepoPr(slug),
  mergeRepoPr: (slug: string) => mockMergeRepoPr(slug),
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { ReposPage } from "../ReposPage";
import type { AgentRepo } from "@/api/agent";

const REPO_CLEAN: AgentRepo = {
  slug: "org/clean",
  branch: "main",
  dirty: false,
  ahead: 0,
  behind: 0,
};

const REPO_AHEAD: AgentRepo = {
  slug: "org/ahead",
  branch: "feature/x",
  dirty: false,
  ahead: 3,
  behind: 0,
};

const REPO_WITH_PR: AgentRepo = {
  slug: "org/with-pr",
  branch: "feature/y",
  ahead: 1,
  behind: 0,
  pr_number: 42,
  pr_url: "https://github.com/org/with-pr/pull/42",
};

function renderWithQueryClient(repos: AgentRepo[]) {
  mockFetchRepos.mockResolvedValue(repos);
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <ReposPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockFetchRepos.mockReset();
  mockPushRepo.mockReset();
  mockPushRepoPr.mockReset();
  mockMergeRepoPr.mockReset();
  vi.spyOn(window, "confirm").mockImplementation(() => true);
});

describe("ReposPage", () => {
  it("renders empty state when no repos", async () => {
    renderWithQueryClient([]);
    await waitFor(() => {
      expect(screen.getByText(/no repos/i)).toBeInTheDocument();
    });
  });

  it("renders a row per repo with slug + branch", async () => {
    renderWithQueryClient([REPO_CLEAN, REPO_AHEAD]);

    await waitFor(() => {
      expect(screen.getByTestId("repo-row-org/clean")).toBeInTheDocument();
    });
    expect(screen.getByTestId("repo-row-org/clean")).toHaveTextContent("main");
    expect(screen.getByTestId("repo-row-org/ahead")).toHaveTextContent(
      "feature/x",
    );
  });

  it("push action confirms and calls pushRepo with the row's slug", async () => {
    mockPushRepo.mockResolvedValue(REPO_AHEAD);
    renderWithQueryClient([REPO_AHEAD]);

    await waitFor(() =>
      expect(screen.getByTestId("repo-row-org/ahead")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByLabelText("Push org/ahead"));

    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringMatching(/push.*org\/ahead/i),
    );
    await waitFor(() =>
      expect(mockPushRepo).toHaveBeenCalledWith("org/ahead"),
    );
  });

  it("push-pr action confirms and calls pushRepoPr with the row's slug", async () => {
    mockPushRepoPr.mockResolvedValue(REPO_AHEAD);
    renderWithQueryClient([REPO_AHEAD]);

    await waitFor(() =>
      expect(screen.getByTestId("repo-row-org/ahead")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByLabelText("Push PR org/ahead"));

    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringMatching(/push pr.*org\/ahead/i),
    );
    await waitFor(() =>
      expect(mockPushRepoPr).toHaveBeenCalledWith("org/ahead"),
    );
  });

  it("merge-pr action confirms and calls mergeRepoPr with the row's slug", async () => {
    mockMergeRepoPr.mockResolvedValue(REPO_WITH_PR);
    renderWithQueryClient([REPO_WITH_PR]);

    await waitFor(() =>
      expect(screen.getByTestId("repo-row-org/with-pr")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByLabelText("Merge PR org/with-pr"));

    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringMatching(/merge pr.*org\/with-pr/i),
    );
    await waitFor(() =>
      expect(mockMergeRepoPr).toHaveBeenCalledWith("org/with-pr"),
    );
  });

  it("merge PR button is disabled when the repo has no open PR", async () => {
    renderWithQueryClient([REPO_AHEAD]);

    await waitFor(() =>
      expect(screen.getByTestId("repo-row-org/ahead")).toBeInTheDocument(),
    );

    const mergeBtn = screen.getByLabelText("Merge PR org/ahead");
    expect(mergeBtn).toBeDisabled();
  });

  it("cancelling the confirm dialog does not call the mutation", async () => {
    vi.spyOn(window, "confirm").mockImplementation(() => false);
    renderWithQueryClient([REPO_AHEAD]);

    await waitFor(() =>
      expect(screen.getByTestId("repo-row-org/ahead")).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByLabelText("Push org/ahead"));

    expect(window.confirm).toHaveBeenCalled();
    expect(mockPushRepo).not.toHaveBeenCalled();
  });
});
