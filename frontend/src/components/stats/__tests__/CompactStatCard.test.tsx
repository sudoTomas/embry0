import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CompactStatCard } from "../CompactStatCard";

describe("CompactStatCard", () => {
  it("renders title and value", () => {
    render(<CompactStatCard title="Running" value="3" />);
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("renders subtitle when provided", () => {
    render(<CompactStatCard title="Spent" value="$4.20" subtitle="Month $87" />);
    expect(screen.getByText("Month $87")).toBeInTheDocument();
  });

  it("applies the pulse class when pulse is true", () => {
    const { container } = render(
      <CompactStatCard title="Running" value="3" pulse />,
    );
    expect(container.querySelector(".animate-pulse-glow")).not.toBeNull();
  });

  it("does not apply the pulse class when pulse is false", () => {
    const { container } = render(<CompactStatCard title="Idle" value="0" />);
    expect(container.querySelector(".animate-pulse-glow")).toBeNull();
  });
});
