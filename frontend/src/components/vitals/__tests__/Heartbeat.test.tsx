import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { Heartbeat } from "../Heartbeat";

describe("Heartbeat", () => {
  it("renders an accessible label describing the live pulse", () => {
    const { getByRole } = render(<Heartbeat label="orchestrator live" />);
    expect(getByRole("status")).toHaveAccessibleName(/orchestrator live/i);
  });

  it("does not reuse the legacy 16px athanor-card chrome", () => {
    const { container } = render(<Heartbeat label="x" />);
    expect(container.querySelector(".athanor-card")).toBeNull();
  });

  it("drives the pulse via the reduced-motion-safe vitals-pulse class", () => {
    const { container } = render(<Heartbeat label="x" />);
    expect(container.querySelector(".vitals-pulse")).not.toBeNull();
  });

  it("never falls back to animate-pulse-glow (whose keyframes ignore prefers-reduced-motion)", () => {
    const { container } = render(<Heartbeat label="x" />);
    expect(container.querySelector(".animate-pulse-glow")).toBeNull();
  });

  it("colors the pulse with the primary token (not a hardcoded hex)", () => {
    const { container } = render(<Heartbeat label="x" />);
    const dot = container.querySelector(".vitals-pulse");
    expect(dot?.className).toMatch(/bg-primary/);
  });
});
