import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { DivineOperation } from "../DivineOperation";

describe("DivineOperation", () => {
  it("calcinate applies the strut-pulse class to the strut group", () => {
    const { container } = render(<DivineOperation operation="calcinate" />);
    expect(container.querySelector(".divine-op-calcinate-struts")).not.toBeNull();
  });

  it("dissolve applies the dissolve-ring class to the outer ring", () => {
    const { container } = render(<DivineOperation operation="dissolve" />);
    expect(container.querySelector(".divine-op-dissolve-ring")).not.toBeNull();
  });

  it("separate applies top + bottom hemisphere classes", () => {
    const { container } = render(<DivineOperation operation="separate" />);
    expect(container.querySelector(".divine-op-separate-top")).not.toBeNull();
    expect(container.querySelector(".divine-op-separate-bottom")).not.toBeNull();
  });

  it("conjoin applies the conjoin-hemis class to the joined hemispheres group", () => {
    const { container } = render(<DivineOperation operation="conjoin" />);
    expect(container.querySelector(".divine-op-conjoin-hemis")).not.toBeNull();
  });

  it("ferment applies the ferment-core class to the center", () => {
    const { container } = render(<DivineOperation operation="ferment" />);
    expect(container.querySelector(".divine-op-ferment-core")).not.toBeNull();
  });

  it("distill applies the three staggered distill ring classes", () => {
    const { container } = render(<DivineOperation operation="distill" />);
    expect(container.querySelector(".divine-op-distill-1")).not.toBeNull();
    expect(container.querySelector(".divine-op-distill-2")).not.toBeNull();
    expect(container.querySelector(".divine-op-distill-3")).not.toBeNull();
  });

  it("coagulate applies the coagulate-fill class to the filled outer circle", () => {
    const { container } = render(<DivineOperation operation="coagulate" />);
    expect(container.querySelector(".divine-op-coagulate-fill")).not.toBeNull();
  });

  it("carries the divine-element class for the escape hatch", () => {
    const { container } = render(<DivineOperation operation="calcinate" />);
    expect(container.querySelector(".divine-element")).not.toBeNull();
  });

  it("uses currentColor on the outer ring so the gold token drives it", () => {
    const { container } = render(<DivineOperation operation="calcinate" />);
    const ring = container.querySelector('svg circle[r="22"][stroke="currentColor"]');
    expect(ring).not.toBeNull();
  });

  it("respects size prop", () => {
    const { container } = render(<DivineOperation operation="calcinate" size={120} />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("width")).toBe("120");
    expect(svg?.getAttribute("height")).toBe("120");
  });

  it("returns null when prefers-reduced-motion is reduce", () => {
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
    const { container } = render(<DivineOperation operation="ferment" />);
    expect(container.querySelector("svg")).toBeNull();
    window.matchMedia = original;
  });
});
