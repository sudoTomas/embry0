import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { AlchemicalSigil } from "../AlchemicalSigil";
import { CARDINAL_HEMISPHERES } from "@/lib/sigils";

describe("AlchemicalSigil", () => {
  it("renders an SVG for a known stage", () => {
    const { container } = render(<AlchemicalSigil stage="develop" />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute("viewBox")).toBe("0 0 64 64");
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

describe("hemisphere-lit variants (geodesic identity)", () => {
  it.each([
    ["triage", "north"],
    ["develop", "east"],
    ["validate", "south"],
    ["qa", "west"],
  ] as const)("renders %s with %s hemisphere lit", (stage, position) => {
    const { container } = render(<AlchemicalSigil stage={stage} />);
    const svg = container.querySelector("svg");
    expect(svg?.innerHTML).toContain(`data-hemisphere="${position}"`);
  });

  it("renders all four cardinal dots on every in-scope stage", () => {
    for (const stage of ["triage", "develop", "validate", "qa"] as const) {
      const { container } = render(<AlchemicalSigil stage={stage} />);
      const dots = container.querySelectorAll('svg circle[r="2.4"]');
      expect(dots, `stage=${stage}`).toHaveLength(4);
    }
  });

  it("preserves legacy classical glyphs for explore and publish", () => {
    const { container } = render(<AlchemicalSigil stage="explore" />);
    expect(container.querySelector("svg")?.innerHTML).not.toContain('data-hemisphere');
  });

  it("CARDINAL_HEMISPHERES maps the 4 in-scope stages to N/E/S/W", () => {
    expect(CARDINAL_HEMISPHERES).toEqual({
      triage: "north",
      develop: "east",
      validate: "south",
      qa: "west",
    });
  });
});
