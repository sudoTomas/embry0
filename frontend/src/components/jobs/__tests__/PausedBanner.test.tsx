import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PausedBanner } from "../PausedBanner";

describe("PausedBanner budget", () => {
  it("says how much more a continue lets a job over its budget spend", async () => {
    const onResume = vi.fn();
    render(
      <PausedBanner
        jobId="job-1"
        reason="max_retries"
        budget={{ spent: 11.49, cap: 10, continue_adds: 10 }}
        onResume={onResume}
        onDiscard={vi.fn()}
      />,
    );
    expect(screen.getByTestId("paused-budget")).toHaveTextContent(
      "Over budget: $11.49 spent of $10.00. Continue lets it spend $10.00 more.",
    );
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(onResume).toHaveBeenCalledWith("continue_retrying", undefined);
  });

  it("shows no budget line for a pause within budget", () => {
    render(
      <PausedBanner
        jobId="job-1"
        reason="dev_branch_missing"
        onResume={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("paused-budget")).not.toBeInTheDocument();
  });
});
