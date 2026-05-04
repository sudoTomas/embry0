import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { Badge } from "../Badge";

describe("Badge", () => {
  it("renders its children", () => {
    render(<Badge>passed</Badge>);
    expect(screen.getByText("passed")).toBeInTheDocument();
  });

  it("applies the success tone classes", () => {
    render(<Badge tone="success">ok</Badge>);
    const el = screen.getByText("ok");
    expect(el.className).toContain("text-success");
    expect(el.className).toContain("bg-success/10");
  });

  it("falls back to neutral tone when no tone is provided", () => {
    render(<Badge>x</Badge>);
    const el = screen.getByText("x");
    expect(el.className).toContain("text-white/70");
  });

  it("merges a custom className", () => {
    render(
      <Badge tone="gold" className="ml-2">
        athanor
      </Badge>,
    );
    const el = screen.getByText("athanor");
    expect(el.className).toContain("ml-2");
    expect(el.className).toContain("text-primary");
  });
});
