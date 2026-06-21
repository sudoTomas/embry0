import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { VitalsTile } from "../VitalsTile";

describe("VitalsTile", () => {
  it("renders label and value", () => {
    render(<VitalsTile label="QUEUE DEPTH" value="42" />);
    expect(screen.getByText("QUEUE DEPTH")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
  });

  it("uses the 8px rounded-lg primitive (not the legacy 16px athanor-card)", () => {
    const { container } = render(<VitalsTile label="x" value="1" />);
    const root = container.firstElementChild;
    expect(root?.className).toMatch(/\brounded-lg\b/);
    expect(root?.className).not.toMatch(/\bathanor-card\b/);
  });

  it("never reuses the athanor-card chrome anywhere in the subtree", () => {
    const { container } = render(<VitalsTile label="x" value="1" />);
    expect(container.querySelector(".athanor-card")).toBeNull();
  });

  it("renders the optional trend when supplied", () => {
    render(<VitalsTile label="latency" value="120ms" trend="+8% vs 1h" />);
    expect(screen.getByText("+8% vs 1h")).toBeInTheDocument();
  });

  it("omits the trend slot entirely when no trend is supplied", () => {
    const { container } = render(<VitalsTile label="x" value="1" />);
    expect(container.querySelector('[data-slot="trend"]')).toBeNull();
  });
});
