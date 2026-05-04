import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { OperationGlyph } from "../OperationGlyph";
import { OPERATIONS } from "../operations";

describe("OperationGlyph", () => {
  it("renders a static SVG (no animation classes)", () => {
    const { container } = render(<OperationGlyph operation="calcinate" />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    // Static — must NOT carry the animation class names
    expect(container.querySelector(".divine-op-calcinate-struts")).toBeNull();
  });

  it.each(OPERATIONS)("renders for every operation: %s", (op) => {
    const { container } = render(<OperationGlyph operation={op} />);
    expect(container.querySelector("svg")).not.toBeNull();
  });

  it("uses currentColor so the gold token drives the stroke/fill", () => {
    const { container } = render(<OperationGlyph operation="calcinate" />);
    const ring = container.querySelector('svg circle[r="22"]');
    expect(ring?.getAttribute("stroke")).toBe("currentColor");
  });

  it("carries the divine-element class for the escape hatch", () => {
    const { container } = render(<OperationGlyph operation="ferment" />);
    expect(container.querySelector(".divine-element")).not.toBeNull();
  });

  it("respects size prop (default 32, override applies)", () => {
    const { container: cDefault } = render(<OperationGlyph operation="distill" />);
    expect(cDefault.querySelector("svg")?.getAttribute("width")).toBe("32");

    const { container: cLarge } = render(<OperationGlyph operation="distill" size={64} />);
    expect(cLarge.querySelector("svg")?.getAttribute("width")).toBe("64");
  });

  it("renders even when prefers-reduced-motion is reduce (static, not motion)", () => {
    const original = window.matchMedia;
    window.matchMedia = ((q: string) =>
      ({
        matches: q.includes("reduce"),
        media: q,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      } as unknown as MediaQueryList)) as typeof window.matchMedia;
    const { container } = render(<OperationGlyph operation="ferment" />);
    expect(container.querySelector("svg")).not.toBeNull();
    window.matchMedia = original;
  });

  it("includes a title element when titled prop is set, for tooltip/screen-reader access", () => {
    const { container } = render(<OperationGlyph operation="distill" titled />);
    expect(container.querySelector("title")?.textContent).toMatch(/distill/i);
  });
});
