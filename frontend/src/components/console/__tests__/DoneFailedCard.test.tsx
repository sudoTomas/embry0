import { describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "review", display_name: "Reviewer", avatar: "warden", color: "rose", tagline: "" }], isLoading: false }),
}));
import { DoneFailedCard } from "../DoneFailedCard";
import { makeJob } from "./fixtures";
import type { JobResponse } from "@/lib/types";

function renderCard(job: JobResponse) {
  return render(
    <MemoryRouter>
      <DoneFailedCard job={job} />
    </MemoryRouter>,
  );
}

describe("DoneFailedCard", () => {
  it("renders a PR pill linking to the pull request", () => {
    renderCard(
      makeJob({ status: "completed", pr_url: "https://github.com/acme/widgets/pull/7" }),
    );

    const pill = screen.getByTestId("pr-pill");
    expect(pill).toHaveAttribute("href", "https://github.com/acme/widgets/pull/7");
    expect(screen.queryByTestId("no-pr")).toBeNull();
  });

  it('renders an explicit "no PR" when there is no deliverable', () => {
    renderCard(makeJob({ status: "completed", pr_url: null }));

    expect(screen.getByTestId("no-pr")).toHaveTextContent("no PR");
    expect(screen.queryByTestId("pr-pill")).toBeNull();
  });

  it("shows the status sub-badge (distinguishing merged from completed)", () => {
    renderCard(makeJob({ status: "pr_merged", pr_url: "https://github.com/acme/widgets/pull/7" }));
    expect(screen.getByText("pr_merged")).toBeInTheDocument();
  });

  it("says what became of the PR, independent of the job status", () => {
    renderCard(
      makeJob({
        status: "failed",
        delivery_state: "merged",
        pr_url: "https://github.com/acme/widgets/pull/7",
      }),
    );

    expect(screen.getByTestId("pr-pill")).toHaveTextContent("PR merged");
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it.each([
    ["closed", "PR closed"],
    ["pr_open", "PR open"],
    ["something-newer", "PR"],
    [undefined, "PR"],
  ])("labels the PR pill for delivery_state %s", (delivery_state, label) => {
    renderCard(
      makeJob({
        status: "completed",
        delivery_state,
        pr_url: "https://github.com/acme/widgets/pull/7",
      }),
    );
    expect(screen.getByTestId("pr-pill")).toHaveTextContent(new RegExp(`^${label}$`));
  });

  it("surfaces error_code as a chip on failed jobs", () => {
    renderCard(
      makeJob({ status: "failed", error_code: "ERR_MAX_AGENT_QUESTIONS", error_message: "cap hit" }),
    );

    expect(screen.getByText("ERR_MAX_AGENT_QUESTIONS")).toBeInTheDocument();
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it("renders no error chip when error_code is null", () => {
    renderCard(makeJob({ status: "cancelled", error_code: null }));
    expect(screen.queryByText(/ERR_/)).toBeNull();
  });

  it("shows the last stage's sprite done for a successful job and failed otherwise", () => {
    renderCard(makeJob({ status: "completed", current_stage: "reviewer", finished_at: "2026-07-08T11:00:00Z" }));
    let sprite = screen.getByTestId("party-sprite").querySelector("svg")!;
    expect(sprite).toHaveAttribute("data-avatar", "warden");
    expect(sprite).toHaveAttribute("data-state", "done");
    cleanup();
    renderCard(makeJob({ status: "failed", current_stage: "reviewer", finished_at: "2026-07-08T11:00:00Z" }));
    sprite = screen.getByTestId("party-sprite").querySelector("svg")!;
    expect(sprite).toHaveAttribute("data-state", "failed");
  });
  it("shows no sprite for a legacy row without a recorded stage", () => {
    renderCard(makeJob({ status: "completed", current_stage: null }));
    expect(screen.queryByTestId("party-sprite")).toBeNull();
  });
});
