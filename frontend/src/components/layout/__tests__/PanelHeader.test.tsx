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

  it("renders the equator-only lead glyph by default (no operation prop)", () => {
    const { container } = render(<PanelHeader title="x" />);
    // Default lead = ring + equator line, NO operation-specific frame elements
    const cardinalDots = container.querySelectorAll('svg circle[r="2.4"]');
    expect(cardinalDots.length).toBe(0);
  });

  it("swaps the lead glyph for an OperationGlyph when operation prop is set", () => {
    const { container } = render(<PanelHeader title="x" operation="distill" />);
    // OperationGlyph renders cardinal dots; the default lead does not
    const cardinalDots = container.querySelectorAll('svg circle[r="2.4"]');
    expect(cardinalDots.length).toBeGreaterThanOrEqual(4);
    // Distill renders three concentric inner rings
    const distillRings = container.querySelectorAll('svg circle[r="6"], svg circle[r="11"], svg circle[r="16"]');
    expect(distillRings.length).toBe(3);
  });

  it("includes an aria title on the operation glyph for screen readers", () => {
    const { container } = render(<PanelHeader title="x" operation="ferment" />);
    expect(container.querySelector("title")?.textContent).toMatch(/ferment/i);
  });
});
