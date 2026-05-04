import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PanelHeader } from "../PanelHeader";

describe("PanelHeader", () => {
  it("renders the supplied title", () => {
    render(<PanelHeader title="JOBS · ACTIVE" />);
    expect(screen.getByText("JOBS · ACTIVE")).toBeInTheDocument();
  });

  it("renders the optional trailing slot", () => {
    render(<PanelHeader title="JOBS" trailing="12" />);
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("renders the equator-only lead glyph (ring + horizontal line, no dots, no hemispheres)", () => {
    const { container } = render(<PanelHeader title="x" />);
    const svg = container.querySelector("svg");
    expect(svg?.querySelector('circle[r="22"]')).not.toBeNull();
    expect(svg?.querySelector('line')).not.toBeNull();
    expect(svg?.querySelectorAll('circle[r="2.4"]').length).toBe(0);
  });

  it("carries the divine-element class", () => {
    const { container } = render(<PanelHeader title="x" />);
    expect(container.querySelector(".divine-element")).not.toBeNull();
  });
});
