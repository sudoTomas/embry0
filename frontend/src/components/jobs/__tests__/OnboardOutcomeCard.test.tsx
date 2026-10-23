import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { OnboardOutcomeCard } from "../OnboardOutcomeCard";

function renderCard(job: {
  pipeline_template?: string | null;
  status: string;
  repo?: string | null;
  pipeline_config?: Record<string, unknown> | null;
}) {
  return render(
    <MemoryRouter>
      <OnboardOutcomeCard job={job} />
    </MemoryRouter>,
  );
}

describe("OnboardOutcomeCard", () => {
  it("links a completed onboard job to the stored test setup", () => {
    renderCard({ pipeline_template: "onboard", status: "completed", repo: "group/sub/proj" });
    expect(screen.getByTestId("onboard-outcome-config-cta")).toHaveAttribute(
      "href",
      "/qa/repos/group%2Fsub%2Fproj/config",
    );
    expect(screen.getByTestId("onboard-outcome")).toHaveTextContent("stored a validated test setup");
  });

  it("sends a completed propose-mode job to the proposal on the repository page", () => {
    renderCard({
      pipeline_template: "onboard",
      status: "completed",
      repo: "group/sub/proj",
      pipeline_config: { branch: "main", proposal_id: "prop-0001" },
    });
    expect(screen.getByTestId("onboard-outcome-proposal-cta")).toHaveAttribute("href", "/repos/group/sub/proj");
    expect(screen.getByTestId("onboard-outcome")).toHaveTextContent("proposed a test setup");
    expect(screen.queryByTestId("onboard-outcome-config-cta")).toBeNull();
  });

  it("offers the by-hand page, prefilled with the repo, when an onboard job failed", () => {
    renderCard({ pipeline_template: "onboard", status: "failed", repo: "owner/name" });
    expect(screen.getByTestId("onboard-outcome-manual-cta")).toHaveAttribute("href", "/repos/onboard?repo=owner%2Fname");
    expect(screen.queryByTestId("onboard-outcome-config-cta")).toBeNull();
  });

  it("renders nothing for other pipelines or while the job runs", () => {
    const { container } = renderCard({ pipeline_template: "qa", status: "completed", repo: "owner/name" });
    expect(container).toBeEmptyDOMElement();
    renderCard({ pipeline_template: "onboard", status: "running", repo: "owner/name" });
    expect(screen.queryByTestId("onboard-outcome")).toBeNull();
  });
});
