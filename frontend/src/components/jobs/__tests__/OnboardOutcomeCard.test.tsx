import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { OnboardOutcomeCard } from "../OnboardOutcomeCard";

function renderCard(job: { pipeline_template?: string | null; status: string; repo?: string | null }) {
  return render(
    <MemoryRouter>
      <OnboardOutcomeCard job={job} />
    </MemoryRouter>,
  );
}

describe("OnboardOutcomeCard", () => {
  it("links a completed onboard job to the stored config", () => {
    renderCard({ pipeline_template: "onboard", status: "completed", repo: "group/sub/proj" });
    expect(screen.getByTestId("onboard-outcome-config-cta")).toHaveAttribute(
      "href",
      "/qa/repos/group%2Fsub%2Fproj/config",
    );
  });

  it("offers the manual form, prefilled with the repo, when an onboard job failed", () => {
    renderCard({ pipeline_template: "onboard", status: "failed", repo: "owner/name" });
    expect(screen.getByTestId("onboard-outcome-manual-cta")).toHaveAttribute(
      "href",
      "/repos/onboard?repo=owner%2Fname&mode=manual",
    );
    expect(screen.queryByTestId("onboard-outcome-config-cta")).toBeNull();
  });

  it("renders nothing for other pipelines or while the job runs", () => {
    const { container } = renderCard({ pipeline_template: "qa", status: "completed", repo: "owner/name" });
    expect(container).toBeEmptyDOMElement();
    renderCard({ pipeline_template: "onboard", status: "running", repo: "owner/name" });
    expect(screen.queryByTestId("onboard-outcome")).toBeNull();
  });
});
