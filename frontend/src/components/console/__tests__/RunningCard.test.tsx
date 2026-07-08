import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

vi.mock("@/hooks/useLiveJobSummary", () => ({
  useLiveJobSummary: vi.fn(),
}));

import { useLiveJobSummary } from "@/hooks/useLiveJobSummary";
import { RunningCard } from "../RunningCard";
import { makeJob, makeSummary } from "./fixtures";

function renderCard(props: Parameters<typeof RunningCard>[0]) {
  return render(
    <MemoryRouter>
      <RunningCard {...props} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(useLiveJobSummary).mockReset().mockReturnValue(makeSummary());
});

describe("RunningCard", () => {
  it("renders repo, 8-char mono job id, and the first task line as title", () => {
    renderCard({ job: makeJob() });

    expect(screen.getByText("acme/widgets")).toBeInTheDocument();
    expect(screen.getByText("abcd1234")).toBeInTheDocument();
    expect(screen.getByText("Fix the notes editor")).toBeInTheDocument();
    expect(screen.queryByText(/Second line/)).toBeNull();
  });

  it("is a link to the job detail page", () => {
    renderCard({ job: makeJob() });
    expect(screen.getByRole("link", { name: "View job abcd1234efgh5678" })).toBeInTheDocument();
  });

  describe("issue chip", () => {
    it("renders no chip when issue_id and issue_number are both null (operator job)", () => {
      renderCard({ job: makeJob({ issue_id: null, issue_number: null }) });
      expect(screen.queryByTestId("issue-chip")).toBeNull();
    });

    it("renders #number when the job has an issue_number", () => {
      renderCard({ job: makeJob({ issue_id: "iss-1", issue_number: 42 }) });
      expect(screen.getByTestId("issue-chip")).toHaveTextContent("#42");
    });

    it("renders a generic chip for issue_id without a GitHub number", () => {
      renderCard({ job: makeJob({ issue_id: "iss-1", issue_number: null }) });
      expect(screen.getByTestId("issue-chip")).toHaveTextContent("issue");
    });
  });

  describe("stage badge", () => {
    it("renders node#attempt from the live summary", () => {
      vi.mocked(useLiveJobSummary).mockReturnValue(makeSummary({ currentNode: "review", attempt: 2 }));
      renderCard({ job: makeJob() });
      expect(screen.getByTestId("stage-badge")).toHaveTextContent("review#2");
    });

    it("omits the badge when neither the stream nor the row knows a stage", () => {
      renderCard({ job: makeJob({ current_stage: null }) });
      expect(screen.queryByTestId("stage-badge")).toBeNull();
    });
  });

  describe("budget meter thresholds", () => {
    it("renders no meter without a cap, showing bare spend", () => {
      vi.mocked(useLiveJobSummary).mockReturnValue(makeSummary({ latestCost: 0.42 }));
      renderCard({ job: makeJob() });

      expect(screen.queryByTestId("budget-meter")).toBeNull();
      expect(screen.getByTestId("cost-line")).toHaveTextContent("$0.42");
    });

    it("shows spent / cap with a success bar below 80%", () => {
      vi.mocked(useLiveJobSummary).mockReturnValue(makeSummary({ latestCost: 0.5 }));
      renderCard({ job: makeJob(), maxBudgetUsd: 1.0 });

      expect(screen.getByTestId("cost-line")).toHaveTextContent("$0.50 / $1.00");
      const bar = screen.getByTestId("budget-meter-bar");
      expect(bar.className).toContain("bg-success");
      expect(bar.style.width).toBe("50%");
    });

    it("turns amber at 80% of the cap", () => {
      vi.mocked(useLiveJobSummary).mockReturnValue(makeSummary({ latestCost: 0.85 }));
      renderCard({ job: makeJob(), maxBudgetUsd: 1.0 });

      expect(screen.getByTestId("budget-meter-bar").className).toContain("bg-warning");
    });

    it("turns red at overrun and caps the bar at 100%", () => {
      vi.mocked(useLiveJobSummary).mockReturnValue(makeSummary({ latestCost: 1.2 }));
      renderCard({ job: makeJob(), maxBudgetUsd: 1.0 });

      const bar = screen.getByTestId("budget-meter-bar");
      expect(bar.className).toContain("bg-destructive");
      expect(bar.style.width).toBe("100%");
    });
  });

  describe("degraded (WS-down) rendering from polled data", () => {
    it("falls back to the jobs row for cost and stage, and never renders blank", () => {
      vi.mocked(useLiveJobSummary).mockReturnValue(
        makeSummary({ isConnected: false, latestCost: 0, currentNode: null, lastActivity: null }),
      );
      renderCard({
        job: makeJob({ total_cost_usd: 0.33, current_stage: "developer" }),
      });

      // Polled stage badge — no attempt suffix (attempts only exist live)
      expect(screen.getByTestId("stage-badge")).toHaveTextContent("developer");
      expect(screen.getByTestId("stage-badge")).not.toHaveTextContent("#");
      // Polled cost from the jobs row
      expect(screen.getByTestId("cost-line")).toHaveTextContent("$0.33");
      // Ticker shows a placeholder rather than vanishing
      expect(screen.getByTestId("activity-ticker")).toHaveTextContent("waiting for activity…");
      // Live dot degrades to a Polling indicator
      expect(screen.getByTestId("live-indicator")).toHaveTextContent("Polling");
    });

    it("shows the pulsing Live indicator while the WS is connected", () => {
      vi.mocked(useLiveJobSummary).mockReturnValue(makeSummary({ isConnected: true }));
      renderCard({ job: makeJob() });

      const indicator = screen.getByTestId("live-indicator");
      expect(indicator).toHaveTextContent("Live");
      expect(indicator.querySelector(".animate-pulse")).not.toBeNull();
    });

    it("prefers live cost over the polled row when the stream has ticked", () => {
      vi.mocked(useLiveJobSummary).mockReturnValue(makeSummary({ latestCost: 0.9 }));
      renderCard({ job: makeJob({ total_cost_usd: 0.33 }) });

      expect(screen.getByTestId("cost-line")).toHaveTextContent("$0.90");
    });
  });
});
