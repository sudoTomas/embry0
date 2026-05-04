import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AthanorMark } from "../AthanorMark";

describe("AthanorMark", () => {
  it("renders the wordmark", () => {
    render(<AthanorMark />);
    expect(screen.getByText("ATHANOR")).toBeInTheDocument();
  });

  it("renders an aria-label so screen readers can identify it", () => {
    const { container } = render(<AthanorMark />);
    const root = container.firstElementChild;
    expect(root?.getAttribute("aria-label")).toBe("Athanor");
  });

  it("includes the divine-element class for the escape hatch", () => {
    const { container } = render(<AthanorMark />);
    expect(container.querySelector(".divine-element")).not.toBeNull();
  });

  it("includes the athanor-mark class on the SVG so the pulse animation targets it", () => {
    const { container } = render(<AthanorMark />);
    expect(container.querySelector(".athanor-mark")).not.toBeNull();
  });

  it("uses the text-primary class so the gold token drives the stroke color", () => {
    const { container } = render(<AthanorMark />);
    const svg = container.querySelector("svg");
    expect(svg?.classList.contains("text-primary")).toBe(true);
  });
});

describe("geodesic mark", () => {
  it("renders the outer ring", () => {
    const { container } = render(<AthanorMark />);
    const ring = container.querySelector('svg circle[r="22"]');
    expect(ring).not.toBeNull();
  });

  it("renders four cardinal dots", () => {
    const { container } = render(<AthanorMark />);
    const dots = container.querySelectorAll('svg circle[r="2.4"]');
    expect(dots).toHaveLength(4);
  });

  it("renders the equator", () => {
    const { container } = render(<AthanorMark />);
    const equator = container.querySelector('svg line[y1="32"][y2="32"]');
    expect(equator).not.toBeNull();
  });

  it("does not render the legacy retort vessel paths", () => {
    const { container } = render(<AthanorMark />);
    expect(container.querySelector('svg circle[r="5.5"]')).toBeNull();
  });
});
