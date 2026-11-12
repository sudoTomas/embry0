import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PausedBanner } from "../PausedBanner";

describe("PausedBanner title", () => {
  it("says only that the job is paused: the reason is the brief card's (#436)", () => {
    render(<PausedBanner jobId="job-1" onResume={vi.fn()} onDiscard={vi.fn()} />);
    const title = screen.getByText(/^(Paused|Pipeline paused|Max retries)/).textContent;
    expect(title, "the banner's title says more than that the job is paused").toBe("Paused");
  });
});

describe("PausedBanner countdown", () => {
  it("counts down to the instant the API says the pause ends (#471)", () => {
    const expiresAt = new Date(Date.now() + 2 * 3600 * 1000 + 5 * 60 * 1000).toISOString();
    render(<PausedBanner jobId="job-1" expiresAt={expiresAt} onResume={vi.fn()} onDiscard={vi.fn()} />);
    expect(screen.queryByText(/^2h \d+m remaining$/), "the banner does not count down to the pause's end").not.toBeNull();
  });

  it("reads Expired once that instant has passed, and shows no countdown without one", () => {
    const { rerender } = render(
      <PausedBanner jobId="job-1" expiresAt={new Date(Date.now() - 1000).toISOString()} onResume={vi.fn()} onDiscard={vi.fn()} />,
    );
    expect(screen.queryByText("Expired"), "a pause past its end does not read as expired").not.toBeNull();
    rerender(<PausedBanner jobId="job-1" onResume={vi.fn()} onDiscard={vi.fn()} />);
    expect(screen.queryByText(/remaining$|^Expired$/), "a countdown without an end to count to").toBeNull();
  });
});

describe("PausedBanner budget", () => {
  it("says how much more a continue lets a job over its budget spend", async () => {
    const onResume = vi.fn();
    render(
      <PausedBanner
        jobId="job-1"
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
        onResume={vi.fn()}
        onDiscard={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("paused-budget")).not.toBeInTheDocument();
  });
});
