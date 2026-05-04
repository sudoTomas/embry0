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

describe("JobStatusRing — cardinal pulse on idle", () => {
  it("applies cardinal-pulse classes to dots when currentStage is null", () => {
    const { container } = render(<JobStatusRing currentStage={null} />);
    expect(container.querySelector('circle.divine-cardinal-pulse-n')).not.toBeNull();
    expect(container.querySelector('circle.divine-cardinal-pulse-e')).not.toBeNull();
    expect(container.querySelector('circle.divine-cardinal-pulse-s')).not.toBeNull();
    expect(container.querySelector('circle.divine-cardinal-pulse-w')).not.toBeNull();
  });

  it("does NOT apply cardinal-pulse classes when currentStage is set", () => {
    const { container } = render(<JobStatusRing currentStage="develop" />);
    expect(container.querySelector('circle.divine-cardinal-pulse-n')).toBeNull();
    expect(container.querySelector('circle.divine-cardinal-pulse-e')).toBeNull();
  });
});

describe("JobStatusRing — scanning prop", () => {
  it("applies divine-equator-scan class to the equator line when scanning is true", () => {
    const { container } = render(<JobStatusRing currentStage="develop" scanning />);
    expect(container.querySelector('line.divine-equator-scan')).not.toBeNull();
  });

  it("does NOT apply scan class when scanning is false or omitted", () => {
    const { container: c1 } = render(<JobStatusRing currentStage="develop" />);
    expect(c1.querySelector('line.divine-equator-scan')).toBeNull();
    const { container: c2 } = render(<JobStatusRing currentStage="develop" scanning={false} />);
    expect(c2.querySelector('line.divine-equator-scan')).toBeNull();
  });

  it("composes idle pulse with scanning when both apply", () => {
    const { container } = render(<JobStatusRing currentStage={null} scanning />);
    expect(container.querySelector('circle.divine-cardinal-pulse-n')).not.toBeNull();
    expect(container.querySelector('line.divine-equator-scan')).not.toBeNull();
  });
});

describe("JobStatusRing — stage shift transitions", () => {
  it("each quarter-arc has the 600ms opacity transition style", () => {
    const { container } = render(<JobStatusRing currentStage="develop" />);
    const arcs = container.querySelectorAll('path[data-arc]');
    expect(arcs.length).toBe(4);
    arcs.forEach((arc) => {
      const style = arc.getAttribute("style") ?? "";
      expect(style).toMatch(/transition.*opacity.*600ms/);
    });
  });
});
