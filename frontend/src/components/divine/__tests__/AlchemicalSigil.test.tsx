import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { AlchemicalSigil } from "../AlchemicalSigil";

describe("AlchemicalSigil", () => {
  it("renders an SVG for a known stage", () => {
    const { container } = render(<AlchemicalSigil stage="develop" />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute("viewBox")).toBe("0 0 24 24");
  });

  it("carries the divine-element class so it can be hidden via the escape hatch", () => {
    const { container } = render(<AlchemicalSigil stage="triage" />);
    expect(container.querySelector(".divine-element")).not.toBeNull();
  });

  it("uses currentColor for stroke so parent text color drives it", () => {
    const { container } = render(<AlchemicalSigil stage="qa" />);
    const svg = container.querySelector("svg");
    expect(svg?.innerHTML).toContain('stroke="currentColor"');
  });
});
