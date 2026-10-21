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
  mockUseRepoPreferences.mockReturnValue({
    data: { ...BASE, pr_review },
    isLoading: false,
    isError: false,
    isSuccess: true,
  });
}

function controls() {
  return [
    screen.getByRole("switch", { name: "Review pull requests" }),
    ...screen.getAllByRole("radio"),
    screen.getByLabelText("Model"),
    screen.getByRole("button", { name: "Save review settings" }),
  ];
}

describe("PullRequestReviewCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    withPrReview(null);
  });

  it("leaves its heading out when the repo page's Advanced section already names it", () => {
    const { unmount } = render(<PullRequestReviewCard repo="acme/widgets" />);
    expect(screen.getByRole("heading", { name: "Pull request review" })).toBeInTheDocument();
    unmount();
    render(<PullRequestReviewCard repo="acme/widgets" showIntro={false} />);
    expect(screen.queryByRole("heading", { name: "Pull request review" })).not.toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Review pull requests" })).toBeInTheDocument();
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
    expect(update).toEqual({
      pr_review: { enabled: true, depth: "targeted", model: "claude-sonnet-5", ready_authors: "team" },
    });
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

  it("locks every control while the stored settings load", () => {
    mockUseRepoPreferences.mockReturnValue({ data: undefined, isLoading: true, isError: false, isSuccess: false });
    render(<PullRequestReviewCard repo="acme/widgets" />);
    for (const control of controls()) {
      const name = control.getAttribute("aria-label") || control.getAttribute("value") || control.textContent;
      expect(control, `${name} is editable while loading`).toBeDisabled();
    }
  });

  it("refuses to save over settings it could not load", async () => {
    const user = userEvent.setup();
    mockUseRepoPreferences.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      isSuccess: false,
      error: new Error("Network Error"),
    });
    render(<PullRequestReviewCard repo="acme/widgets" />);
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/);
    for (const control of controls()) {
      expect(control, "a control is editable after a failed load").toBeDisabled();
    }
    await user.click(screen.getByRole("switch", { name: "Review pull requests" }));
    await user.click(screen.getByRole("button", { name: "Save review settings" }));
    expect(mockMutate, "the defaults were saved over the stored settings").not.toHaveBeenCalled();
  });

  it("shows a stored model that is not in the model list as itself", async () => {
    const user = userEvent.setup();
    withPrReview({ enabled: false, depth: "read_only", model: "claude-retired-1" });
    render(<PullRequestReviewCard repo="acme/widgets" />);
    const model = screen.getByLabelText("Model");
    expect(model, "the select shows another model than the stored one").toHaveValue("claude-retired-1");
    expect(within(model).getByRole("option", { name: /claude-retired-1/ })).toBeInTheDocument();

    await user.click(screen.getByRole("switch", { name: "Review pull requests" }));
    await user.click(screen.getByRole("button", { name: "Save review settings" }));
    await waitFor(() => expect(mockMutate).toHaveBeenCalled());
    expect(mockMutate.mock.calls[0][0].update).toEqual({
      pr_review: { enabled: true, depth: "read_only", model: "claude-retired-1", ready_authors: "team" },
    });
  });

  it("offers who triggers automatic reviews, team by default", () => {
    render(<PullRequestReviewCard repo="acme/widgets" />);
    const group = screen.getByRole("group", { name: "Who triggers automatic reviews" });
    const team = within(group).getByRole("radio", { name: /^Team — people with write access/ });
    const anyone = within(group).getByRole("radio", { name: /^Anyone who opens or readies a pull request/ });
    expect(team, "a repo that never configured it must default to the team").toBeChecked();
    expect(anyone).not.toBeChecked();
    expect(within(group).getByText(/fork pull requests then start read-only reviews on your budget/)).toBeInTheDocument();
  });

  it("reads settings saved without ready_authors as team", () => {
    withPrReview({ enabled: true, depth: "full", model: null });
    render(<PullRequestReviewCard repo="acme/widgets" />);
    expect(screen.getByRole("radio", { name: /^Team — people with write access/ })).toBeChecked();
  });

  it("shows a stored anyone", () => {
    withPrReview({ enabled: true, depth: "full", model: null, ready_authors: "anyone" });
    render(<PullRequestReviewCard repo="acme/widgets" />);
    expect(screen.getByRole("radio", { name: /^Anyone who opens or readies a pull request/ })).toBeChecked();
  });

  it("saves ready_authors in the PUT payload", async () => {
    const user = userEvent.setup();
    withPrReview({ enabled: true, depth: "full", model: null });
    render(<PullRequestReviewCard repo="acme/widgets" />);
    await user.click(screen.getByRole("radio", { name: /^Anyone who opens or readies a pull request/ }));
    await user.click(screen.getByRole("button", { name: "Save review settings" }));
    await waitFor(() => expect(mockMutate).toHaveBeenCalled());
    expect(mockMutate.mock.calls[0][0].update, "ready_authors was not saved").toEqual({
      pr_review: { enabled: true, depth: "full", model: null, ready_authors: "anyone" },
    });
  });

  it("locks the ready-authors choice while the stored settings load", () => {
    mockUseRepoPreferences.mockReturnValue({ data: undefined, isLoading: true, isError: false, isSuccess: false });
    render(<PullRequestReviewCard repo="acme/widgets" />);
    const group = screen.getByRole("group", { name: "Who triggers automatic reviews" });
    for (const radio of within(group).getAllByRole("radio")) {
      expect(radio, `${radio.getAttribute("value")} is editable while loading`).toBeDisabled();
    }
  });
});
