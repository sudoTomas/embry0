import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { DivineRadiate } from "../DivineRadiate";

describe("DivineRadiate", () => {
  it("renders the static base mark (ring + dots + equator)", () => {
    const { container } = render(<DivineRadiate />);
    expect(container.querySelector('svg circle[r="22"]')).not.toBeNull();
    expect(container.querySelectorAll('svg circle[r="2.4"]')).toHaveLength(4);
    expect(container.querySelector('svg line[y1="32"][y2="32"]')).not.toBeNull();
  });

  it("renders 5 north-pole struts marked for animation", () => {
    const { container } = render(<DivineRadiate />);
    const struts = container.querySelectorAll('svg path[data-strut="north-pole"]');
    expect(struts).toHaveLength(5);
  });

  it("carries the divine-element and divine-radiate classes", () => {
    const { container } = render(<DivineRadiate />);
    const root = container.querySelector("svg");
    expect(root?.classList.contains("divine-element")).toBe(true);
    expect(root?.classList.contains("divine-radiate")).toBe(true);
  });

  it("groups the struts in a divine-radiate-struts container so the pulse keyframe targets them", () => {
    const { container } = render(<DivineRadiate />);
    const strutGroup = container.querySelector(".divine-radiate-struts");
    expect(strutGroup).not.toBeNull();
    expect(strutGroup?.querySelectorAll('path[data-strut="north-pole"]')).toHaveLength(5);
  });
});
