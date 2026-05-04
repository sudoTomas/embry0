import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { JobStatusRing } from "../JobStatusRing";

describe("JobStatusRing", () => {
  it("renders all four quarter-arcs", () => {
    const { container } = render(<JobStatusRing currentStage="triage" />);
    const arcs = container.querySelectorAll('svg path[data-arc]');
    expect(arcs).toHaveLength(4);
  });

  it("renders the four cardinal dots", () => {
    const { container } = render(<JobStatusRing currentStage="triage" />);
    expect(container.querySelectorAll('svg circle[r="2.4"]')).toHaveLength(4);
  });

  it("carries the divine-element class so the escape hatch hides it", () => {
    const { container } = render(<JobStatusRing currentStage="triage" />);
    expect(container.querySelector(".divine-element")).not.toBeNull();
  });

  it.each([
    ["triage", "TRG"],
    ["develop", "DEV"],
    ["validate", "REV"],
    ["qa", "QA"],
  ] as const)("displays the 3-letter code for %s as %s in the center", (stage, code) => {
    const { container } = render(<JobStatusRing currentStage={stage} />);
    expect(container.querySelector("svg text")?.textContent).toBe(code);
  });

  it("when currentStage is null, all arcs render at 0.4 opacity and center is blank", () => {
    const { container } = render(<JobStatusRing currentStage={null} />);
    const arcs = container.querySelectorAll('svg path[data-arc]');
    arcs.forEach((arc) => {
      expect(arc.getAttribute("opacity")).toBe("0.4");
    });
    expect(container.querySelector("svg text")?.textContent).toBe("");
  });

  it("active arc gets opacity 1.0; recency-back arcs get 0.75 / 0.4 / 0.15", () => {
    const { container } = render(<JobStatusRing currentStage="develop" />);
    const get = (stage: string) =>
      container.querySelector(`svg path[data-arc="${stage}"]`)?.getAttribute("opacity");
    expect(get("develop")).toBe("1");
    expect(get("triage")).toBe("0.75");
    expect(get("qa")).toBe("0.4");
    expect(get("validate")).toBe("0.15");
  });
});
