import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { Embry0Mark } from "../Embry0Mark";

describe("Embry0Mark", () => {
  it("renders the embry0 wordmark", () => {
    render(<Embry0Mark />);
    expect(screen.getByText("embry0")).toBeInTheDocument();
  });
});
