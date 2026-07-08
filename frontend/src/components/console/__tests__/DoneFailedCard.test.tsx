import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
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
});
