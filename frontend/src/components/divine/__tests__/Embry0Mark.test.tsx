import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { Embry0Mark } from "../Embry0Mark";

describe("Embry0Mark", () => {
  it("renders the embry0 wordmark", () => {
    render(<Embry0Mark />);
    expect(screen.getByRole("img", { name: "embry0" })).toBeInTheDocument();
  });

  it("sets the 0 in the gold accent and the rest in the primary text colour", () => {
    render(<Embry0Mark />);
    expect(screen.getByText("embry").className).toContain("text-fg");
    expect(screen.getByText("0").className).toContain("text-primary");
  });
});
