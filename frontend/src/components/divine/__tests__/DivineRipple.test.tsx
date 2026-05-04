import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { DivineRipple } from "../DivineRipple";

describe("DivineRipple", () => {
  it("renders an SVG with a single ripple circle when motion is allowed", () => {
    const { container } = render(<DivineRipple />);
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.querySelector("circle.divine-ripple-circle")).not.toBeNull();
  });

  it("carries the divine-element class for the escape hatch", () => {
    const { container } = render(<DivineRipple />);
    expect(container.querySelector(".divine-element")).not.toBeNull();
  });

  it("uses currentColor on the ripple stroke so the gold token drives it", () => {
    const { container } = render(<DivineRipple />);
    const circle = container.querySelector("circle.divine-ripple-circle");
    expect(circle?.getAttribute("stroke")).toBe("currentColor");
    expect(circle?.getAttribute("fill")).toBe("none");
  });

  it("renders nothing when prefers-reduced-motion is reduce", () => {
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
    const { container } = render(<DivineRipple />);
    expect(container.querySelector("svg")).toBeNull();
    window.matchMedia = original;
  });

  it("respects size prop", () => {
    const { container } = render(<DivineRipple size={80} />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("width")).toBe("80");
    expect(svg?.getAttribute("height")).toBe("80");
  });
});
