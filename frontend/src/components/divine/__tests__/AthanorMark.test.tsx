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

  it("includes the athanor-vessel class on the SVG so the pulse animation targets it", () => {
    const { container } = render(<AthanorMark />);
    expect(container.querySelector(".athanor-vessel")).not.toBeNull();
  });
});
