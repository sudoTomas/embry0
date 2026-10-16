import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mockUseRepoPreferences = vi.fn();
const mockMutate = vi.fn();

vi.mock("@/hooks/useRepoPreferences", () => ({
  useRepoPreferences: (...a: unknown[]) => mockUseRepoPreferences(...a),
  useSetRepoPreferences: () => ({ mutate: mockMutate, isPending: false }),
}));
vi.mock("@/hooks/useModels", () => ({
  useModels: () => ({ models: ["claude-opus-5", "claude-sonnet-5"], isLoading: false }),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { PullRequestReviewCard } from "../PullRequestReviewCard";

const BASE = {
  repo: "acme/widgets",
  sandbox_profile: null,
  language_hint: null,
  notes: "",
  execution_mode: null,
  auth_mode: null,
  git_author_name: null,
  git_author_email: null,
  dev_checks: [],
  base_branch: null,
  updated_at: "2026-10-15T00:00:00Z",
};

function withPrReview(pr_review: unknown) {
  mockUseRepoPreferences.mockReturnValue({ data: { ...BASE, pr_review }, isLoading: false });
}

describe("PullRequestReviewCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    withPrReview(null);
  });

  it("is off, full depth and on the heavy tier for a repo that never configured it", () => {
    render(<PullRequestReviewCard repo="acme/widgets" />);
    expect(screen.getByRole("switch", { name: "Review pull requests" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("radio", { name: /^Full/ })).toBeChecked();
    const model = screen.getByLabelText("Model");
    expect(model).toHaveValue("");
    expect(within(model).getAllByRole("option")[0]).toHaveTextContent("Default (heavy tier)");
  });

  it("describes each depth on its own line", () => {
    render(<PullRequestReviewCard repo="acme/widgets" />);
    expect(screen.getByText(/run every dev check/)).toBeInTheDocument();
    expect(screen.getByText(/whose paths the pull request touches/)).toBeInTheDocument();
    expect(screen.getByText(/Pull requests from forks are always reviewed this way/)).toBeInTheDocument();
  });

  it("shows the stored settings", () => {
    withPrReview({ enabled: true, depth: "read_only", model: "claude-opus-5" });
    render(<PullRequestReviewCard repo="acme/widgets" />);
    expect(screen.getByRole("switch", { name: "Review pull requests" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: /^Read only/ })).toBeChecked();
    expect(screen.getByLabelText("Model")).toHaveValue("claude-opus-5");
  });

  it("saves the whole settings object and nothing else", async () => {
    const user = userEvent.setup();
    render(<PullRequestReviewCard repo="acme/widgets" />);
    await user.click(screen.getByRole("switch", { name: "Review pull requests" }));
    await user.click(screen.getByRole("radio", { name: /^Targeted/ }));
    await user.selectOptions(screen.getByLabelText("Model"), "claude-sonnet-5");
    await user.click(screen.getByRole("button", { name: "Save review settings" }));

    await waitFor(() => expect(mockMutate).toHaveBeenCalled());
    const { fullRepo, update } = mockMutate.mock.calls[0][0];
    expect(fullRepo).toBe("acme/widgets");
    expect(update).toEqual({ pr_review: { enabled: true, depth: "targeted", model: "claude-sonnet-5" } });
  });

  it("sends null for the default model", async () => {
    const user = userEvent.setup();
    withPrReview({ enabled: true, depth: "full", model: "claude-opus-5" });
    render(<PullRequestReviewCard repo="acme/widgets" />);
    await user.selectOptions(screen.getByLabelText("Model"), "");
    await user.click(screen.getByRole("button", { name: "Save review settings" }));
    await waitFor(() => expect(mockMutate).toHaveBeenCalled());
    expect(mockMutate.mock.calls[0][0].update.pr_review.model).toBeNull();
  });

  it("keeps Save disabled until something changes", () => {
    render(<PullRequestReviewCard repo="acme/widgets" />);
    expect(screen.getByRole("button", { name: "Save review settings" })).toBeDisabled();
  });

  it("lists the webhook events the repository needs", () => {
    render(<PullRequestReviewCard repo="acme/widgets" />);
    const note = screen.getByTestId("pr-review-webhook-note");
    for (const event of ["Pull requests", "Issue comments", "Merge request events", "Comments"]) {
      expect(note).toHaveTextContent(event);
    }
  });
});
